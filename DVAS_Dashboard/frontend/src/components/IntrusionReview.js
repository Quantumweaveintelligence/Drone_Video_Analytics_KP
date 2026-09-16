import React, { useState, useEffect, useMemo } from 'react';
import { api } from '@/lib/api';
import { useAnalyticsFetch } from '@/lib/useAnalytics';
import { 
  Activity, Clock, ShieldAlert, Crosshair, Users, Timer, 
  Download, ChevronLeft, ChevronRight, AlertTriangle, Play
} from 'lucide-react';
import { 
  BarChart, Bar, ResponsiveContainer, 
  XAxis, YAxis, Tooltip, CartesianGrid
} from 'recharts';

// Format time utility
const formatTime = (seconds) => {
  if (!seconds) return '0m 0s';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
};

// Date formatter
const formatDate = (dateStr) => {
  if (!dateStr) return 'N/A';
  return new Date(dateStr).toLocaleString();
};

export default function IntrusionReview({ activeDrone }) {
  const [missions, setMissions] = useState([]);
  const [selectedMissionId, setSelectedMissionId] = useState('');
  const [page, setPage] = useState(1);
  const [loadingMissions, setLoadingMissions] = useState(true);
  const itemsPerPage = 20;

  useEffect(() => {
    const fetchMissions = async () => {
      try {
        setLoadingMissions(true);
        const response = await api.get('/api/missions');
        let fetchedMissions = response.data || [];
        
        // Filter by active drone if provided
        if (activeDrone) {
          fetchedMissions = fetchedMissions.filter(m => String(m.drone_id) === String(activeDrone));
        }
        
        setMissions(fetchedMissions);
        if (fetchedMissions.length > 0) {
          setSelectedMissionId(fetchedMissions[0].id);
        }
      } catch (err) {
        console.error('Failed to fetch missions:', err);
      } finally {
        setLoadingMissions(false);
      }
    };
    fetchMissions();
  }, [activeDrone]);

  const analyticsUrl = selectedMissionId ? `/analytics/intrusion/analytics?mission_id=${selectedMissionId}` : null;
  const { data: analyticsData, loading: analyticsLoading } = useAnalyticsFetch(analyticsUrl, 30000);

  const eventsUrl = selectedMissionId ? `/analytics/intrusion/events?mission_id=${selectedMissionId}` : null;
  const { data: eventsData, loading: eventsLoading } = useAnalyticsFetch(eventsUrl, 30000);

  const selectedMission = useMemo(() => {
    return missions.find(m => String(m.id) === String(selectedMissionId)) || null;
  }, [missions, selectedMissionId]);

  const handleDownloadJson = async () => {
    if (!selectedMissionId) return;
    try {
      const response = await api.get(`/analytics/intrusion/analytics?mission_id=${selectedMissionId}`);
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(response.data, null, 2));
      const downloadAnchorNode = document.createElement('a');
      downloadAnchorNode.setAttribute("href",     dataStr);
      downloadAnchorNode.setAttribute("download", `intrusion_analytics_mission_${selectedMissionId}.json`);
      document.body.appendChild(downloadAnchorNode);
      downloadAnchorNode.click();
      downloadAnchorNode.remove();
    } catch (error) {
      console.error('Error downloading analytics:', error);
    }
  };

  // Extract KPI metrics safely
  const totalEvents = analyticsData?.total_events || 0;
  const uniqueTracks = analyticsData?.unique_tracks || 0;
  const avgDwellTime = analyticsData?.avg_dwell_time || 0;
  const peakConcurrent = analyticsData?.peak_concurrent || 0;
  const sessionDuration = analyticsData?.session_duration || 0;
  
  // Prepare timeline data
  const timelineData = useMemo(() => {
    if (!analyticsData?.timeline) return [];
    return analyticsData.timeline.map(item => ({
      time: new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      ENTERING: item.entering_count || 0,
      DWELLING: item.dwelling_count || 0,
      EXITED: item.exited_count || 0,
      INSIDE: item.inside_count || 0
    }));
  }, [analyticsData]);

  // Paginate events
  const eventsList = eventsData?.events || [];
  const totalPages = Math.ceil(eventsList.length / itemsPerPage) || 1;
  const paginatedEvents = eventsList.slice((page - 1) * itemsPerPage, page * itemsPerPage);

  const getStateColor = (state) => {
    switch (state?.toUpperCase()) {
      case 'ENTERING': return 'bg-amber-500/20 text-amber-400 border-amber-500/50';
      case 'DWELLING': return 'bg-red-500/20 text-red-400 border-red-500/50';
      case 'EXITED': return 'bg-green-500/20 text-green-400 border-green-500/50';
      case 'INSIDE': return 'bg-blue-500/20 text-blue-400 border-blue-500/50';
      default: return 'bg-slate-500/20 text-slate-400 border-slate-500/50';
    }
  };

  if (loadingMissions) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-slate-950 text-slate-300">
        <Activity className="animate-spin h-8 w-8 text-cyan-500 mr-3" />
        <span>Loading missions...</span>
      </div>
    );
  }

  if (missions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full w-full bg-slate-950 text-slate-400">
        <AlertTriangle className="h-12 w-12 text-slate-600 mb-4" />
        <h3 className="text-xl font-medium text-slate-300">No Missions Found</h3>
        <p className="mt-2 text-sm text-slate-500">
          {activeDrone ? `No completed missions found for drone ${activeDrone}.` : 'No completed missions available.'}
        </p>
      </div>
    );
  }

  return (
    <div className="h-full w-full bg-slate-950 text-slate-300 flex flex-col overflow-y-auto">
      {/* 1. Mission Selector */}
      <div className="p-4 border-b border-slate-800 bg-slate-900 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div className="flex flex-col gap-1 w-full md:w-1/3">
          <label className="text-xs text-slate-500 uppercase tracking-wider font-semibold">Select Mission</label>
          <select 
            className="w-full bg-slate-950 border border-slate-800 text-slate-300 rounded p-2 outline-none focus:border-cyan-500 transition-colors"
            value={selectedMissionId}
            onChange={(e) => {
              setSelectedMissionId(e.target.value);
              setPage(1);
            }}
          >
            {missions.map(m => (
              <option key={m.id} value={m.id}>
                {m.name || `Mission ${m.id}`} (Drone: {m.drone_id})
              </option>
            ))}
          </select>
        </div>

        {selectedMission && (
          <div className="flex flex-col md:flex-row gap-4 md:gap-8 w-full md:w-2/3 md:justify-end text-sm">
            <div className="flex flex-col">
              <span className="text-slate-500 text-xs uppercase tracking-wider">Status</span>
              <span className="font-mono text-cyan-400">{selectedMission.status || 'COMPLETED'}</span>
            </div>
            <div className="flex flex-col">
              <span className="text-slate-500 text-xs uppercase tracking-wider">Time Range</span>
              <span className="font-mono text-slate-300">
                {formatDate(selectedMission.start_time)} - {formatDate(selectedMission.end_time)}
              </span>
            </div>
          </div>
        )}
      </div>

      {analyticsLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <Activity className="animate-spin h-8 w-8 text-cyan-500 mr-3" />
          <span>Loading analytics...</span>
        </div>
      ) : (
        <div className="p-4 flex flex-col gap-6">
          
          {/* 2. Summary KPI Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            <div className="bg-slate-900 border border-slate-800 p-4 rounded shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <ShieldAlert className="h-4 w-4 text-cyan-500" />
                <h3 className="text-xs text-slate-500 uppercase tracking-wider">Total Events</h3>
              </div>
              <div className="text-2xl font-mono text-cyan-400">{totalEvents}</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Users className="h-4 w-4 text-purple-500" />
                <h3 className="text-xs text-slate-500 uppercase tracking-wider">Unique Tracks</h3>
              </div>
              <div className="text-2xl font-mono text-cyan-400">{uniqueTracks}</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Crosshair className="h-4 w-4 text-emerald-500" />
                <h3 className="text-xs text-slate-500 uppercase tracking-wider">Detections/Class</h3>
              </div>
              <div className="text-lg font-mono text-cyan-400 truncate">
                {analyticsData?.detections_by_class 
                  ? Object.entries(analyticsData.detections_by_class).map(([c, count]) => `${c}:${count}`).join(', ') || 'N/A'
                  : 'N/A'}
              </div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Clock className="h-4 w-4 text-amber-500" />
                <h3 className="text-xs text-slate-500 uppercase tracking-wider">Avg Dwell Time</h3>
              </div>
              <div className="text-2xl font-mono text-cyan-400">{formatTime(avgDwellTime)}</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Activity className="h-4 w-4 text-red-500" />
                <h3 className="text-xs text-slate-500 uppercase tracking-wider">Peak Concurrent</h3>
              </div>
              <div className="text-2xl font-mono text-cyan-400">{peakConcurrent}</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Timer className="h-4 w-4 text-blue-500" />
                <h3 className="text-xs text-slate-500 uppercase tracking-wider">Session Dur.</h3>
              </div>
              <div className="text-2xl font-mono text-cyan-400">{formatTime(sessionDuration)}</div>
            </div>
          </div>

          {/* 3. Event Timeline Chart */}
          <div className="bg-slate-900 border border-slate-800 rounded p-4 shadow-sm">
            <h3 className="text-sm text-slate-300 uppercase tracking-wider mb-4 font-semibold border-b border-slate-800 pb-2">Event Timeline</h3>
            <div className="h-64 w-full">
              {timelineData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={timelineData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--dvas-border)" vertical={false} />
                    <XAxis dataKey="time" stroke="var(--dvas-text-muted)" fontSize={12} tickLine={false} axisLine={false} />
                    <YAxis stroke="var(--dvas-text-muted)" fontSize={12} tickLine={false} axisLine={false} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: 'var(--dvas-surface)', borderColor: 'var(--dvas-border)', color: 'var(--dvas-text)', borderRadius: 'var(--dvas-radius)' }}
                      itemStyle={{ color: 'var(--dvas-text)' }}
                      cursor={{ fill: 'var(--dvas-elev)', opacity: 0.5 }}
                    />
                    <Bar dataKey="ENTERING" stackId="a" fill="var(--dvas-warn)" name="Entering" />
                    <Bar dataKey="INSIDE" stackId="a" fill="var(--dvas-blue)" name="Inside" />
                    <Bar dataKey="DWELLING" stackId="a" fill="var(--dvas-crit)" name="Dwelling" />
                    <Bar dataKey="EXITED" stackId="a" fill="var(--dvas-safe)" name="Exited" />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-slate-500">
                  No timeline data available for this mission.
                </div>
              )}
            </div>
          </div>

          {/* 4. Per-Track Event Table */}
          <div className="bg-slate-900 border border-slate-800 rounded shadow-sm overflow-hidden">
            <div className="p-4 border-b border-slate-800 flex justify-between items-center">
              <h3 className="text-sm text-slate-300 uppercase tracking-wider font-semibold">Per-Track Events</h3>
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <span>Total: {eventsList.length}</span>
              </div>
            </div>
            
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-950/50 border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
                    <th className="p-3 font-medium">Timestamp</th>
                    <th className="p-3 font-medium">Track ID</th>
                    <th className="p-3 font-medium">Class</th>
                    <th className="p-3 font-medium">State</th>
                    <th className="p-3 font-medium">Confidence</th>
                    <th className="p-3 font-medium">Dwell Dur.</th>
                  </tr>
                </thead>
                <tbody>
                  {eventsLoading ? (
                    <tr>
                      <td colSpan="6" className="p-8 text-center text-slate-500">
                        <Activity className="animate-spin h-6 w-6 text-cyan-500 mx-auto mb-2" />
                        Loading events...
                      </td>
                    </tr>
                  ) : paginatedEvents.length === 0 ? (
                    <tr>
                      <td colSpan="6" className="p-8 text-center text-slate-500">
                        No events found for this mission.
                      </td>
                    </tr>
                  ) : (
                    paginatedEvents.map((ev, idx) => (
                      <tr key={ev.id || idx} className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors text-sm">
                        <td className="p-3 font-mono text-slate-400">{formatDate(ev.timestamp)}</td>
                        <td className="p-3 font-mono text-cyan-400">{ev.track_id}</td>
                        <td className="p-3 text-slate-300">{ev.class_name || ev.class_id || 'Unknown'}</td>
                        <td className="p-3">
                          <span className={`px-2 py-1 rounded text-xs font-medium border ${getStateColor(ev.state)}`}>
                            {ev.state || 'UNKNOWN'}
                          </span>
                        </td>
                        <td className="p-3 font-mono text-slate-400">
                          {ev.confidence ? `${(ev.confidence * 100).toFixed(1)}%` : 'N/A'}
                        </td>
                        <td className="p-3 font-mono text-slate-400">
                          {formatTime(ev.dwell_time)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            
            {/* Pagination */}
            {totalPages > 1 && (
              <div className="p-3 border-t border-slate-800 flex justify-between items-center bg-slate-950/30">
                <span className="text-xs text-slate-500">
                  Page {page} of {totalPages}
                </span>
                <div className="flex gap-1">
                  <button 
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="p-1 rounded bg-slate-800 text-slate-300 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed border border-slate-700"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <button 
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="p-1 rounded bg-slate-800 text-slate-300 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed border border-slate-700"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 5. Export Section */}
          <div className="pt-4 border-t border-slate-800 flex justify-end gap-3 pb-8">
            <button 
              onClick={handleDownloadJson}
              disabled={analyticsLoading || !analyticsData}
              className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded border border-slate-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
            >
              <Download className="h-4 w-4" />
              Download Analytics JSON
            </button>
          </div>
          
        </div>
      )}
    </div>
  );
}
