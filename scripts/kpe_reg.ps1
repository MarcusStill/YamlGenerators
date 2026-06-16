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

    try {
    $response = Invoke-WebRequest -Uri "http://$server/svc/mdc/yaml/apply" `
                                  -Method POST `
                                  -Headers $headers `
                                  -InFile $file `
                                  -UseBasicParsing

    # Если статус OK, выводим успех
    Write-Host "Файл '$file' успешно отправлен." -ForegroundColor Green
    Write-Host "Статус: $($response.StatusCode)"
}
catch {
    # Обрабатываем ошибку и декодируем тело ответа
    $errorResponse = $_.Exception.Response

    if ($errorResponse -ne $null) {
        $reader = New-Object System.IO.StreamReader($errorResponse.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        $reader.Close()

        # Пробуем распарсить JSON
        try {
            $jsonObj = $responseBody | ConvertFrom-Json

            # Декодируем Unicode последовательности
            $decodedMessage = [System.Text.RegularExpressions.Regex]::Unescape($jsonObj.message)

            Write-Host "`nОшибка: $($jsonObj.status)" -ForegroundColor Red
            Write-Host "Сообщение:" -ForegroundColor Yellow
            Write-Host $decodedMessage -ForegroundColor White
            if ($jsonObj.correlationId) {
                Write-Host "`nCorrelation ID: $($jsonObj.correlationId)" -ForegroundColor Cyan
            }
        }
        catch {
            # Если не JSON, выводим как есть, но декодируем
            $decodedBody = [System.Text.RegularExpressions.Regex]::Unescape($responseBody)
            Write-Host "Ошибка при отправке файла:" -ForegroundColor Red
            Write-Host $decodedBody -ForegroundColor White
        }
    }
    else {
        Write-Host "Произошла ошибка при отправке файла" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor White
    }
}

Write-Host "`n----------------------------------------------------------------------------------`n"

    Write-Host "Файл '$file' отправлен.`n----------------------------------------------------------------------------------`n"
}