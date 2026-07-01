[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$LlmArgs = @(),
    [string]$Distro = "ubuntu",
    [string]$RepoDir = ""
)

$ErrorActionPreference = "Stop"

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'`"`"'") + "'"
}

if ($RepoDir) {
    $repoExpr = ConvertTo-BashSingleQuoted $RepoDir
} else {
    $repoExpr = '${QWENDEX_WSL_REPO:-$HOME/Qwendex}'
}

$llmArgString = ($LlmArgs | ForEach-Object { ConvertTo-BashSingleQuoted $_ }) -join " "
$llmCommand = "scripts/llm"
if ($llmArgString) {
    $llmCommand = "$llmCommand $llmArgString"
}

$wslCommand = "cd $repoExpr && exec $llmCommand"
$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(
    "wsl -d `"$Distro`" -- bash -lc `"$wslCommand`""
))

Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-EncodedCommand", $encodedCommand
)
