[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8

$user = "user"
$pass = "pass"
$server = "localhost:8191"

$pair = "$user`:$pass"
$authBytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
$authBase64 = [Convert]::ToBase64String($authBytes)

$headers = @{
    "Authorization" = "Basic $authBase64"
    "Content-Type"  = "text/yaml"
    "Accept"        = "application/json"
}

while ($true) {
	Write-Host "KPE $server"
    $file = Read-Host "Укажите путь к файлу (или Enter/q для выхода)"

    if ([string]::IsNullOrWhiteSpace($file) -or $file -eq 'q') {
        break
    }

    if (-not (Test-Path $file)) {
        Write-Host "Файл '$file' не найден. Попробуйте ещё раз."
        continue
    }

    Invoke-WebRequest -Uri "http://$server/svc/mdc/yaml/apply" `
                      -Method POST `
                      -Headers $headers `
                      -InFile $file `
                      -UseBasicParsing

    Write-Host "Файл '$file' отправлен.`n----------------------------------------------------------------------------------`n"
}