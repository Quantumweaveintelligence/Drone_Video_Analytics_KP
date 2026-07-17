# test_api.ps1
# ===============================================================================
# Kerala Police Drone Analytics -- PowerShell API Test Script
# Run from project root:  D:\POLICE_DRONE\drone_analytics\
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File test_api.ps1
#   powershell -ExecutionPolicy Bypass -File test_api.ps1 -Username operator_002 -Password Operator@002
# ===============================================================================

param(
    [string]$Username    = "operator_001",
    [string]$Password    = "Operator@001",
    [string]$DroneId     = "DRONE_KL_001",
    [string]$MissionId   = "MISSION_TEST_001",
    [string]$BaseUrl     = "https://localhost",
    [string]$KeycloakUrl = "http://localhost:8080",
    [string]$FramePath   = "test_frame.jpg"
)

# -- Ignore self-signed cert errors (PS 5.x fallback) --------------------------
if ($PSVersionTable.PSVersion.Major -lt 7) {
    Add-Type @"
        using System.Net;
        using System.Security.Cryptography.X509Certificates;
        public class TrustAll : ICertificatePolicy {
            public bool CheckValidationResult(ServicePoint sp, X509Certificate cert,
                WebRequest req, int problem) { return true; }
        }
"@
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAll
    [System.Net.ServicePointManager]::SecurityProtocol  = [System.Net.SecurityProtocolType]::Tls12
}

function Invoke-API {
    param($Method, $Url, $Headers = @{}, $Body = $null, $ContentType = "application/json")
    $params = @{ Method = $Method; Uri = $Url; Headers = $Headers }
    if ($PSVersionTable.PSVersion.Major -ge 7) { $params.SkipCertificateCheck = $true }
    if ($Body) { $params.Body = $Body; $params.ContentType = $ContentType }
    try {
        return Invoke-RestMethod @params
    } catch {
        Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
        if ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            Write-Host "  Body : $($reader.ReadToEnd())" -ForegroundColor DarkRed
        }
        return $null
    }
}

function Write-OK   { param($t) Write-Host "  [OK]   $t" -ForegroundColor Green }
function Write-Fail { param($t) Write-Host "  [FAIL] $t" -ForegroundColor Red }
function Write-Warn { param($t) Write-Host "  [WARN] $t" -ForegroundColor Yellow }
function Write-Step { param($t) Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }

# ===============================================================================
$amp = [char]38   # '&' encoded to avoid PS5 parse error

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Kerala Police Drone Analytics -- API Test" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

# -- Step 1: Fetch client secret from Keycloak Admin ---------------------------
Write-Step "Step 1 - Fetch Client Secret from Keycloak Admin"

$adminBody = "grant_type=password${amp}client_id=admin-cli${amp}username=admin${amp}password=Admin@KP2025!"
try {
    $adminResp  = Invoke-RestMethod -Method POST `
        -Uri "$KeycloakUrl/realms/master/protocol/openid-connect/token" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $adminBody
    $adminToken = $adminResp.access_token
} catch {
    Write-Fail "Keycloak Admin unreachable: $($_.Exception.Message)"
    Write-Host "  Hint: docker compose ps" -ForegroundColor Yellow
    exit 1
}

if (-not $adminToken) {
    Write-Fail "Could not get Keycloak Admin token."
    exit 1
}
Write-OK "Keycloak Admin token obtained"

$clients = Invoke-RestMethod `
    -Uri "$KeycloakUrl/admin/realms/kerala-police-drone/clients?clientId=drone-ingest-client" `
    -Headers @{ Authorization = "Bearer $adminToken" }

if (-not $clients -or $clients.Count -eq 0) {
    Write-Fail "drone-ingest-client not found. Run start-docker.ps1 first."
    exit 1
}

$clientUUID   = $clients[0].id
$secretObj    = Invoke-RestMethod `
    -Uri "$KeycloakUrl/admin/realms/kerala-police-drone/clients/$clientUUID/client-secret" `
    -Headers @{ Authorization = "Bearer $adminToken" }
$ClientSecret = $secretObj.value
Write-OK "Client Secret: $ClientSecret"

# -- Step 2: Get operator bearer token -----------------------------------------
Write-Step "Step 2 - Get Bearer Token (operator: $Username)"

$tokenBody = "grant_type=password${amp}client_id=drone-ingest-client${amp}client_secret=$ClientSecret${amp}username=$Username${amp}password=$Password"
try {
    $tokenResp = Invoke-RestMethod -Method POST `
        -Uri "$KeycloakUrl/realms/kerala-police-drone/protocol/openid-connect/token" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $tokenBody
    $TOKEN = $tokenResp.access_token
} catch {
    Write-Fail "Token request failed: $($_.Exception.Message)"
    exit 1
}

if (-not $TOKEN) {
    Write-Fail "No token returned. Check username/password."
    exit 1
}
Write-OK "Bearer token obtained (expires in $($tokenResp.expires_in)s)"
Write-Host "  Preview: $($TOKEN.Substring(0,40))..." -ForegroundColor DarkGray

# Save the FULL token to a file and copy to clipboard
$TOKEN | Out-File -FilePath "token.txt" -Encoding ascii -NoNewline
$TOKEN | Set-Clipboard
Write-OK "Full token saved to token.txt and copied to clipboard"
Write-Host "  --> Paste directly into Swagger UI 'Authorize' box (just the token, no 'Bearer ' prefix)" -ForegroundColor Yellow

$AuthHeader = @{ Authorization = "Bearer $TOKEN" }

# -- Step 3: Health checks (no auth needed) ------------------------------------
Write-Step "Step 3 - Health Checks"

$health = Invoke-API -Method GET -Url "$BaseUrl/health"
if ($health) { Write-OK "Health: $($health | ConvertTo-Json -Compress)" }
else         { Write-Fail "Health endpoint unreachable" }

$ready = Invoke-API -Method GET -Url "$BaseUrl/health/ready"
if ($ready) { Write-OK "Readiness: $($ready | ConvertTo-Json -Compress)" }
else        { Write-Fail "Readiness endpoint unreachable" }

# -- Step 4: Ingest status (system-wide) ---------------------------------------
Write-Step "Step 4 - Ingest Status (system-wide)"

$status = Invoke-API -Method GET -Url "$BaseUrl/ingest/status" -Headers $AuthHeader
if ($status) { Write-OK "Status: $($status | ConvertTo-Json -Compress)" }
else         { Write-Fail "Could not get ingest status" }

# -- Step 5: Per-drone status --------------------------------------------------
Write-Step "Step 5 - Drone Status for $DroneId"

$droneStatus = Invoke-API -Method GET -Url "$BaseUrl/ingest/status/$DroneId" -Headers $AuthHeader
if ($droneStatus) { Write-OK "Drone Status: $($droneStatus | ConvertTo-Json -Compress)" }
else              { Write-Warn "No status for $DroneId yet (no frames ingested)" }

# -- Step 6: Start mission -----------------------------------------------------
Write-Step "Step 6 - Start Mission ($MissionId)"

$missionBody = @{ drone_id = $DroneId; mission_id = $MissionId; operator = $Username } | ConvertTo-Json

$missionStart = Invoke-API -Method POST `
    -Url "$BaseUrl/ingest/mission/start" `
    -Headers $AuthHeader `
    -Body $missionBody `
    -ContentType "application/json"

if ($missionStart) { Write-OK "Mission started: $($missionStart | ConvertTo-Json -Compress)" }
else               { Write-Fail "Could not start mission" }

# -- Step 7: Upload a test frame via edge_sender.py ---------------------------
Write-Step "Step 7 - Upload Frame via edge_sender.py"

# Delete the 19-byte stub if it exists
if (Test-Path $FramePath) {
    $stubSize = (Get-Item $FramePath).Length
    if ($stubSize -lt 100) {
        Remove-Item $FramePath -Force
        Write-Host "  Removed stub file ($stubSize bytes) - replacing with valid JPEG" -ForegroundColor Yellow
    }
}

if (-not (Test-Path $FramePath)) {
    Write-Host "  Creating a minimal valid JPEG..." -ForegroundColor Yellow
    $jpegBytes = [byte[]](
        0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,0x01,0x00,0x00,0x01,
        0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,0x00,0x08,0x06,0x06,0x07,0x06,0x05,0x08,
        0x07,0x07,0x07,0x09,0x09,0x08,0x0A,0x0C,0x14,0x0D,0x0C,0x0B,0x0B,0x0C,0x19,0x12,
        0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,0x1A,0x1C,0x1C,0x20,0x24,0x2E,0x27,0x20,
        0x22,0x2C,0x23,0x1C,0x1C,0x28,0x37,0x29,0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,
        0x39,0x3D,0x38,0x32,0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,
        0x00,0x01,0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,0x01,0x05,0x01,0x01,
        0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01,0x02,0x03,0x04,
        0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0xFF,0xC4,0x00,0xB5,0x10,0x00,0x02,0x01,0x03,
        0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,0x01,0x7D,0x01,0x02,0x03,0x00,
        0x04,0x11,0x05,0x12,0x21,0x31,0x41,0x06,0x13,0x51,0x61,0x07,0x22,0x71,0x14,0x32,
        0x81,0x91,0xA1,0x08,0x23,0x42,0xB1,0xC1,0x15,0x52,0xD1,0xF0,0x24,0x33,0x62,0x72,
        0x82,0x09,0x0A,0x16,0x17,0x18,0x19,0x1A,0x25,0x26,0x27,0x28,0x29,0x2A,0x34,0x35,
        0x36,0x37,0x38,0x39,0x3A,0x43,0x44,0x45,0x46,0x47,0x48,0x49,0x4A,0x53,0x54,0x55,
        0x56,0x57,0x58,0x59,0x5A,0x63,0x64,0x65,0x66,0x67,0x68,0x69,0x6A,0x73,0x74,0x75,
        0x76,0x77,0x78,0x79,0x7A,0x83,0x84,0x85,0x86,0x87,0x88,0x89,0x8A,0x92,0x93,0x94,
        0x95,0x96,0x97,0x98,0x99,0x9A,0xA2,0xA3,0xA4,0xA5,0xA6,0xA7,0xA8,0xA9,0xAA,0xB2,
        0xB3,0xB4,0xB5,0xB6,0xB7,0xB8,0xB9,0xBA,0xC2,0xC3,0xC4,0xC5,0xC6,0xC7,0xC8,0xC9,
        0xCA,0xD2,0xD3,0xD4,0xD5,0xD6,0xD7,0xD8,0xD9,0xDA,0xE1,0xE2,0xE3,0xE4,0xE5,0xE6,
        0xE7,0xE8,0xE9,0xEA,0xF1,0xF2,0xF3,0xF4,0xF5,0xF6,0xF7,0xF8,0xF9,0xFA,0xFF,0xDA,
        0x00,0x08,0x01,0x01,0x00,0x00,0x3F,0x00,0xFB,0x28,0xA2,0x80,0x03,0xFF,0xD9
    )
    [System.IO.File]::WriteAllBytes((Join-Path (Get-Location) $FramePath), $jpegBytes)
    Write-OK "Created minimal JPEG at $FramePath"
}

# Use edge_sender.py (httpx) which handles multipart correctly
$senderPath = Join-Path (Get-Location) "ingest\edge_sender.py"
if (Test-Path $senderPath) {
    Write-Host "  Running: python ingest\edge_sender.py" -ForegroundColor Gray
    $senderOut = python ingest\edge_sender.py `
        --username $Username `
        --password $Password `
        --drone-id $DroneId `
        --mission-id $MissionId `
        --frame $FramePath `
        --score 0.75 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Frame sent via edge_sender.py"
        Write-Host "  $senderOut" -ForegroundColor DarkGray
    } else {
        Write-Fail "edge_sender.py failed"
        Write-Host "  $senderOut" -ForegroundColor DarkRed
    }
} else {
    Write-Warn "edge_sender.py not found at $senderPath - skipping frame upload"
}

# -- Step 8: Stop mission ------------------------------------------------------
Write-Step "Step 8 - Stop Mission ($MissionId)"

$stopBody  = @{ drone_id = $DroneId; mission_id = $MissionId; frames_sent = 1 } | ConvertTo-Json
$missionStop = Invoke-API -Method POST `
    -Url "$BaseUrl/ingest/mission/end" `
    -Headers $AuthHeader `
    -Body $stopBody `
    -ContentType "application/json"

if ($missionStop) { Write-OK "Mission stopped: $($missionStop | ConvertTo-Json -Compress)" }
else              { Write-Fail "Could not stop mission" }

# -- Step 9: Prometheus metrics ------------------------------------------------
Write-Step "Step 9 - Prometheus Metrics"

try {
    $mParams = @{ Method = "GET"; Uri = "http://localhost:9090/metrics" }
    if ($PSVersionTable.PSVersion.Major -ge 7) { $mParams.SkipCertificateCheck = $true }
    $metrics = Invoke-RestMethod @mParams
    $mlines  = ($metrics -split "`n" | Where-Object { $_ -notmatch "^#" -and $_ -ne "" })
    Write-OK "Metrics live - $($mlines.Count) metric lines"
    $mlines | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
} catch {
    Write-Fail "Metrics endpoint unreachable: $($_.Exception.Message)"
}

# -- Done ----------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Test complete! Check [OK]/[FAIL] labels above." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Useful follow-up:" -ForegroundColor White
Write-Host "    docker compose logs -f ingest-api" -ForegroundColor Gray
Write-Host "    docker compose logs -f gpu-consumer" -ForegroundColor Gray
Write-Host "    docker compose logs -f analytics" -ForegroundColor Gray
Write-Host ""
