# Called by "Create desktop shortcut.bat": Desktop shortcut to this BirdNET eBird folder.
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'BirdNET eBird.lnk'))
$shortcut.TargetPath = Join-Path $root 'python\pythonw.exe'
$shortcut.Arguments = '"' + (Join-Path $root 'app\birdnet_app.py') + '"'
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = (Join-Path $root 'app\assets\AppIcon.ico') + ',0'
$shortcut.Description = 'BirdNET to eBird clipper'
$shortcut.Save()
Write-Host "Created: $desktop\BirdNET eBird.lnk"
