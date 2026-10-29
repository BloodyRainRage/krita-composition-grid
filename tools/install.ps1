#Requires -Version 5.1
<#
.SYNOPSIS
    Установка плагина Krita «Composition Grid» в каталог pykrita.

.DESCRIPTION
    Копирует файл регистрации composition_grid.desktop и Python-пакет
    composition_grid/ в целевой каталог pykrita (по умолчанию
    %APPDATA%\krita\pykrita — стандартный путь Krita на Windows).

    Что копируется (проверено по composition_grid.desktop):
      * X-KDE-Library=composition_grid  -> Krita импортирует пакет
        <pykrita>\composition_grid\__init__.py, поэтому .desktop-файл и папка
        пакета должны лежать в ОДНОМ каталоге;
      * X-Krita-Manual=composition_grid/Manual.html -> справка берётся из
        самой папки пакета, отдельного копирования не требуется.

    Безопасность:
      * скрипт создаёт только собственные каталоги назначения;
      * существующие целевые файлы плагина ПЕРЕЗАПИСЫВАЮТСЯ (Copy-Item -Force),
        всё остальное в pykrita не трогается;
      * НИКАКИХ удалений: Remove-Item в скрипте нет ни в целевой папке, ни вне
        её. Файлы другой версии плагина, оставшиеся в pykrita, не вычищаются;
      * артефакты разработки (__pycache__, .venv, .pytest_cache, .git, *.pyc,
        *.pyo) в установку НЕ попадают;
      * скрипт не запускает Krita и ничего в ней не включает — это делается
        вручную (Settings > Configure Krita... > Python Plugin Manager).

.PARAMETER Destination
    Каталог pykrita. По умолчанию %APPDATA%\krita\pykrita. Нужен, например, для
    переносной сборки Krita или проверки установки во временную папку.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\install.ps1 -Destination "D:\KritaPortable\pykrita"
#>
[CmdletBinding()]
param(
    [string]$Destination
)

$ErrorActionPreference = 'Stop'

# Консоль Windows по кодовой странице cp866/cp437 показывает кириллицу
# «кракозябрами», хотя сам скрипт в UTF-8. Сообщения (в т.ч. строку
# «Скопировано файлов: N», которую сверяет чек-лист) должны читаться,
# поэтому кодировка вывода ставится явно. Ошибки недоступности консоля
# (перенаправление в файл, ISE) не считаются фатальными: это косметика.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    Write-Verbose "install.ps1: console output encoding could not be set: $_"
}

# --- Константы установки ----------------------------------------------------

$script:PluginFolderName = 'composition_grid'
$script:DesktopFileName = 'composition_grid.desktop'

# Каталоги-артефакты (сравниваются по имени сегмента, регистронезависимо).
$script:ExcludedDirectoryNames = @(
    '__pycache__',
    '.venv',
    '.pytest_cache',
    '.git'
)

# Файлы-артефакты (сравниваются с именем файла, регистронезависимо).
$script:ExcludedFileLeafPatterns = @(
    '*.pyc',
    '*.pyo'
)

# --- Вспомогательные функции ------------------------------------------------

function Get-DefaultDestination {
    <#
    .SYNOPSIS
        %APPDATA%\krita\pykrita с явной ошибкой, если APPDATA недоступна.
    .DESCRIPTION
        Не подставляет пустую переменную окружения: иначе получился бы путь
        вида '\krita\pykrita' на диске текущего каталога.
    #>
    $appData = $env:APPDATA
    if ([string]::IsNullOrWhiteSpace($appData)) {
        throw (
            'Переменная окружения APPDATA пуста или не определена. ' +
            'Укажите каталог назначения явно: -Destination "C:\Users\<you>\AppData\Roaming\krita\pykrita"'
        )
    }
    return (Join-Path -Path $appData -ChildPath 'krita\pykrita')
}

function Test-ExcludedRelativePath {
    <#
    .SYNOPSIS
        True, если относительный путь содержит исключённый каталог-артефакт или
        имя файла совпадает с исключённым шаблоном.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [string[]]$ExcludedDirectoryNames = $script:ExcludedDirectoryNames,
        [string[]]$ExcludedFileLeafPatterns = $script:ExcludedFileLeafPatterns
    )

    $segments = @($RelativePath -split '[\\/]' | Where-Object { $_ -ne '' })
    if ($segments.Count -eq 0) {
        return $true
    }

    # Все сегменты, кроме последнего, — это каталоги.
    $segmentCount = $segments.Count
    for ($index = 0; $index -lt ($segmentCount - 1); $index++) {
        if ($ExcludedDirectoryNames -contains $segments[$index]) {
            return $true
        }
    }

    $leaf = $segments[($segmentCount - 1)]
    foreach ($pattern in $ExcludedFileLeafPatterns) {
        if ($leaf -like $pattern) {
            return $true
        }
    }

    return $false
}

function Copy-PluginFile {
    <#
    .SYNOPSIS
        Копирует один файл, создав родительский каталог назначения.
    .DESCRIPTION
        Перезапись целевого файла разрешена (-Force); ничего не удаляется.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceFile,
        [Parameter(Mandatory = $true)][string]$TargetFile
    )

    $targetDirectory = Split-Path -Path $TargetFile -Parent
    if (-not (Test-Path -LiteralPath $targetDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    }
    Copy-Item -LiteralPath $SourceFile -Destination $TargetFile -Force
}

# --- Тело скрипта -----------------------------------------------------------

try {
    if ([string]::IsNullOrWhiteSpace($Destination)) {
        $Destination = Get-DefaultDestination
    }
    # Путь нормализуется в абсолютный: относительный -Destination не должен
    # зависеть от текущего каталога вызова.
    $Destination = [System.IO.Path]::GetFullPath($Destination)

    # tools/install.ps1 лежит в репозитории на уровень ниже корня.
    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        throw (
            'Не удалось определить расположение скрипта ($PSScriptRoot). ' +
            'Запускайте его как файл: powershell -File .\tools\install.ps1'
        )
    }
    $repositoryRoot = Split-Path -Path $PSScriptRoot -Parent

    $desktopSource = Join-Path -Path $repositoryRoot -ChildPath $script:DesktopFileName
    $packageSource = Join-Path -Path $repositoryRoot -ChildPath $script:PluginFolderName

    if (-not (Test-Path -LiteralPath $desktopSource -PathType Leaf)) {
        throw "Не найден файл регистрации плагина: $desktopSource"
    }
    if (-not (Test-Path -LiteralPath $packageSource -PathType Container)) {
        throw "Не найдена папка пакета плагина: $packageSource"
    }

    Write-Output "Источник : $repositoryRoot"
    Write-Output "Назначение: $Destination"
    Write-Output ''

    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        Write-Output "Каталог pykrita создан: $Destination"
    }

    # 1. Файл регистрации (.desktop) — рядом с папкой пакета.
    $desktopTarget = Join-Path -Path $Destination -ChildPath $script:DesktopFileName
    Copy-PluginFile -SourceFile $desktopSource -TargetFile $desktopTarget
    Write-Output "  + $script:DesktopFileName"

    # Счётчик печатает ЧИСЛО ФАЙЛОВ, реально оказавшихся в цели: 1 файл
    # регистрации + все файлы пакета. Печатается в конце, когда $copiedCount
    # уже не меняется, и согласован с подписью «(файл регистрации + пакет …)».
    $copiedCount = 1
    $skippedCount = 0

    # 2. Python-пакет плагина: только файлы исходников, без артефактов.
    $packageTarget = Join-Path -Path $Destination -ChildPath $script:PluginFolderName
    $sourcePrefix = $packageSource.TrimEnd('\', '/')
    $packageFiles = @(Get-ChildItem -LiteralPath $packageSource -Recurse -File -Force)

    if ($packageFiles.Count -eq 0) {
        throw "Папка пакета пуста (файлов не найдено): $packageSource"
    }

    foreach ($file in $packageFiles) {
        $relativePath = $file.FullName.Substring($sourcePrefix.Length).TrimStart('\', '/')
        if (Test-ExcludedRelativePath -RelativePath $relativePath) {
            $skippedCount++
            continue
        }
        Copy-PluginFile -SourceFile $file.FullName -TargetFile (Join-Path -Path $packageTarget -ChildPath $relativePath)
        $copiedCount++
        Write-Output "  + $script:PluginFolderName\$relativePath"
    }

    Write-Output ''
    Write-Output "Скопировано файлов: $copiedCount (файл регистрации + пакет $script:PluginFolderName)."
    Write-Output "Исключено артефактов разработки: $skippedCount ($($script:ExcludedDirectoryNames -join ', '), $($script:ExcludedFileLeafPatterns -join ', '))."

    if (-not (Test-Path -LiteralPath (Join-Path -Path $packageTarget -ChildPath '__init__.py') -PathType Leaf)) {
        Write-Warning (
            "В установленном пакете нет __init__.py ($packageTarget) — Krita не " +
            'сможет импортировать плагин. Проверьте, что копирование началось с корня репозитория.'
        )
    }

    Write-Output ''
    Write-Output 'Обязательные шаги после копирования:'
    Write-Output '  1. В Krita: Settings > Configure Krita... > Python Plugin Manager,'
    Write-Output '     найти и включить «Composition Grid», нажать OK.'
    Write-Output '  2. ПЕРЕЗАПУСТИТЬ Krita: без перезапуска Extension не создаётся,'
    Write-Output '     поэтому экшены (Tools > Scripts) и докер не появятся.'
    Write-Output '  3. Панель включается в списке докеров (Settings > Dockers):'
    Write-Output '     докер регистрируется под идентификатором composition_grid_docker.'
    Write-Output ''
    Write-Output 'Удаление плагина: снимите галочку в Python Plugin Manager и при'
    Write-Output 'необходимости вручную уберите composition_grid.desktop и папку'
    Write-Output "composition_grid из $Destination (этот скрипт ничего не удаляет)."
}
catch {
    # Только вывод + код возврата: Write-Error при $ErrorActionPreference='Stop'
    # сам стал бы пробрасывающейся ошибкой внутри обработчика.
    Write-Output ''
    Write-Output "Установка не выполнена: $($_.Exception.Message)"
    exit 1
}

exit 0
