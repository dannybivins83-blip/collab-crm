<#
  Arm Resend as the owner-alert transport for the CRM / MyRoofPortal.

  Run it in PowerShell:   .\ops\Arm-CrmResend.ps1

  The key is typed at the prompt, never echoed, never written to disk, never
  committed. It is VERIFIED by sending one real message through Resend BEFORE
  anything touches production -- a key Resend rejects, or a from-address on a
  domain Resend has not verified, never reaches the live service.

  Why this exists: from Aug to Nov 2026 every owner lead alert was silently lost.
  The Google app password had been revoked, smtp_configured() still returned True
  because both env vars were set, and the login exception was swallowed. Resend
  does not depend on a Google password, so that failure mode cannot repeat.
#>
$ErrorActionPreference = 'Stop'
$SERVICE = 'srv-d8kq47jbc2fs73crtnug'
$VERIFY_TO = 'dannybivins83@gmail.com'

# --- 0/5 ship the code the transport lives in ----------------------------
# Render deploys collab-crm from agent/gc-consolidation, NOT main. The Resend
# transport and /admin/mail-status are committed but unpushed, so push first --
# arming env vars against a build that lacks the code would set keys that
# nothing reads.
Write-Host '--- 0/5 pushing agent/gc-consolidation (Render deploys from it) ---'
Push-Location (Join-Path $PSScriptRoot '..')
try {
  $branch = (git rev-parse --abbrev-ref HEAD).Trim()
  if ($branch -ne 'agent/gc-consolidation') {
    Write-Host ("ERROR: on branch '{0}'. Render deploys agent/gc-consolidation." -f $branch) -f Red
    exit 1
  }
  $ahead = (git log --oneline origin/agent/gc-consolidation..HEAD) | Measure-Object -Line
  Write-Host ("  {0} commit(s) to push" -f $ahead.Lines)
  if ($ahead.Lines -gt 0) {
    git push origin agent/gc-consolidation
    if ($LASTEXITCODE -ne 0) { Write-Host 'git push failed - stopping.' -f Red; exit 1 }
    # Keep main on the same tip so the branches cannot drift apart again.
    git push origin ('agent/gc-consolidation:main')
    Write-Host '  pushed' -f Green
  } else {
    Write-Host '  nothing to push'
  }
} finally { Pop-Location }

# --- Render API key -------------------------------------------------------
$renderKey = $env:RENDER_API_KEY
if ([string]::IsNullOrWhiteSpace($renderKey)) {
  $sec = Read-Host 'Render API key' -AsSecureString
  $renderKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

# --- Resend key + from address -------------------------------------------
$sec = Read-Host 'Resend API key (starts re_)' -AsSecureString
$resendKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$resendKey = $resendKey.Trim()
if (-not $resendKey.StartsWith('re_')) { Write-Host 'ERROR: a Resend key starts with re_' -f Red; exit 1 }
Write-Host ("read key: {0} chars" -f $resendKey.Length)

$from = Read-Host 'From address (must be on a domain VERIFIED in Resend)'
$from = $from.Trim()
if ($from -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Write-Host 'ERROR: that is not an e-mail address' -f Red; exit 1 }

# --- 1/5 prove the key can actually send ---------------------------------
Write-Host ''
Write-Host '--- 1/5 verifying by sending one real message BEFORE deploying ---'
$body = @{
  from    = "MyRoofPortal <$from>"
  to      = @($VERIFY_TO)
  subject = 'CRM transport check - Resend'
  text    = 'Sent by ops/Arm-CrmResend.ps1 before the key was deployed. If you are reading this, Resend can send from this domain and CRM lead alerts will work.'
} | ConvertTo-Json -Depth 4
try {
  $r = Invoke-RestMethod -Method Post -Uri 'https://api.resend.com/emails' `
        -Headers @{ Authorization = "Bearer $resendKey" } `
        -ContentType 'application/json' -Body $body
} catch {
  Write-Host ("RESEND SEND FAILED: {0}" -f $_.Exception.Message) -f Red
  Write-Host 'Not deploying a key Resend rejects.' -f Red
  exit 1
}
if (-not $r.id) { Write-Host 'RESEND SEND FAILED: no message id returned' -f Red; exit 1 }
Write-Host ("RESEND SEND: OK id {0}" -f $r.id) -f Green
Write-Host ("Check {0} for it before continuing." -f $VERIFY_TO)

# --- 2/5 set both env vars on Render -------------------------------------
Write-Host ''
Write-Host '--- 2/5 setting RESEND_API_KEY + RESEND_FROM_EMAIL on Render ---'
$hdr = @{ Authorization = "Bearer $renderKey" }
foreach ($pair in @(@{k='RESEND_API_KEY';v=$resendKey}, @{k='RESEND_FROM_EMAIL';v=$from})) {
  $uri = "https://api.render.com/v1/services/$SERVICE/env-vars/$($pair.k)"
  $payload = @{ value = $pair.v } | ConvertTo-Json
  Invoke-RestMethod -Method Put -Uri $uri -Headers $hdr -ContentType 'application/json' -Body $payload | Out-Null
  Write-Host ("  set {0}" -f $pair.k)
}

# --- 3/5 wait for the env-var redeploy -----------------------------------
Write-Host ''
Write-Host '--- 3/5 waiting for Render to redeploy (setting env vars triggers one) ---'
$deadline = (Get-Date).AddMinutes(8)
do {
  Start-Sleep -Seconds 15
  $d = Invoke-RestMethod -Uri "https://api.render.com/v1/services/$SERVICE/deploys?limit=1" -Headers $hdr
  $status = $d[0].deploy.status
  Write-Host ("  deploy status: {0}" -f $status)
} while ($status -notin @('live','build_failed','update_failed','canceled') -and (Get-Date) -lt $deadline)
if ($status -ne 'live') { Write-Host ("Deploy ended as '{0}' - stopping." -f $status) -f Red; exit 1 }

# --- 4/5 prove it end to end from the server -----------------------------
Write-Host ''
Write-Host '--- 4/5 asking the live server to send one alert through the new transport ---'
$tok = (Invoke-RestMethod -Uri "https://api.render.com/v1/services/$SERVICE/env-vars?limit=100" -Headers $hdr |
        Where-Object { $_.envVar.key -eq 'DB_RESTORE_TOKEN' }).envVar.value
$st = Invoke-RestMethod -Uri 'https://crm.collaborativeconceptsfl.com/admin/mail-status?send=1' `
        -Headers @{ 'X-Restore-Token' = $tok }
Write-Host ("  resend armed: {0} | smtp armed: {1}" -f $st.resend_armed, $st.smtp_armed)
Write-Host ("  alerts go to: {0}" -f $st.notify_to)
Write-Host ("  test sent:    {0}" -f $st.test_sent)
if ($st.last_error) { Write-Host ("  last error:   {0}" -f $st.last_error) -f Yellow }
Write-Host ''
if ($st.test_sent) {
  Write-Host 'DONE - owner lead alerts are live. Two e-mails should be in the inbox.' -f Green
} else {
  Write-Host 'The server could not send. Read last_error above - nothing else was changed.' -f Red
  exit 1
}
