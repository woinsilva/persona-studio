param([switch]$Force)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$modelsDir = Join-Path $projectDir 'models'

function Get-Model([string]$Url, [string]$Destination) {
  if ((Test-Path -LiteralPath $Destination) -and -not $Force) {
    Write-Host "Já existe: $Destination"
    return
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
  Write-Host "Baixando $(Split-Path -Leaf $Destination)..."
  & curl.exe -L --fail --retry 4 --continue-at - --output $Destination $Url
  if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar $Url" }
}

Get-Model 'https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors' (Join-Path $modelsDir 'checkpoints/RealVisXL_V4.0.safetensors')
Get-Model 'https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin' (Join-Path $modelsDir 'instantid/ip-adapter.bin')
Get-Model 'https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors' (Join-Path $modelsDir 'controlnet/instantid-controlnet.safetensors')

$insightDir = Join-Path $modelsDir 'insightface/models'
$antelopeDir = Join-Path $insightDir 'antelopev2'
if ((Test-Path -LiteralPath $antelopeDir) -and -not $Force) {
  Write-Host "Já existe: $antelopeDir"
} else {
  New-Item -ItemType Directory -Force -Path $insightDir | Out-Null
  $zip = Join-Path $env:TEMP 'persona-studio-antelopev2.zip'
  Get-Model 'https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip' $zip
  if (Test-Path -LiteralPath $antelopeDir) { Remove-Item -LiteralPath $antelopeDir -Recurse -Force }
  Expand-Archive -LiteralPath $zip -DestinationPath $insightDir -Force
  Remove-Item -LiteralPath $zip -Force
}

Write-Host "Modelos prontos em $modelsDir"
