import React, { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

/**
 * Evidence image served by GET /api/evidence/{id}/objects/{role} (evidence plan E5).
 * Fetched with the Authorization header and shown from a blob URL, so no token is put
 * in the image URL and the browser does not cache the bytes.
 * onError receives { target: <img> } like a native image error, so existing fallbacks keep working.
 */
export default function EvidenceImage({ evidenceId, role = "primary", alt = "", className = "", onError }) {
  const [src, setSrc] = useState("");
  const imgRef = useRef(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    let objectUrl = "";
    let cancelled = false;
    setSrc("");
    api
      .get(`/evidence/${encodeURIComponent(evidenceId)}/objects/${encodeURIComponent(role)}`, { responseType: "blob" })
      .then((res) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(res.data);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled && onErrorRef.current && imgRef.current) onErrorRef.current({ target: imgRef.current });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [evidenceId, role]);

  return <img ref={imgRef} src={src || undefined} alt={alt} className={className} onError={onError} />;
}
