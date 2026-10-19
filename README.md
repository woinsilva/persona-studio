# Persona Studio

Aplicação local para criar fotografias realistas mantendo a identidade de uma pessoa a partir de uma única foto de referência.

## Como funciona

- **Ollama (`qwen3:8b`)** melhora a descrição em português e a transforma em um prompt fotográfico.
- **ComfyUI + InstantID** extrai a identidade facial e condiciona o modelo de imagem sem treinamento.
- **RealVisXL/SDXL** produz a fotografia localmente na GPU.
- O app em **FastAPI** guarda personagens, histórico e resultados no diretório `data/`.

O Ollama sozinho não gera pixels; por isso ele atua junto do modelo de difusão. Tudo roda localmente e a foto de referência não é enviada a serviços externos.

## Pré-requisitos

- Docker Desktop com suporte à GPU NVIDIA
- Aproximadamente 20 GB livres para modelos e imagens
- GPU NVIDIA com 10 GB ou mais de VRAM (configuração ajustada para RTX 4070 Ti 12 GB)

O Ollama usa `keep_alive=0` para devolver a VRAM antes da geração SDXL; não aumente esse valor em placas de 12 GB.

## Início rápido (Windows PowerShell)

```powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File .\scripts\setup-models.ps1
docker compose up -d --build
```

Abra [http://localhost:8080](http://localhost:8080). O primeiro build do ComfyUI pode levar alguns minutos.

Este computador já possui o volume `video_ollama_data`, com `qwen3:8b`; o Compose o reutiliza por padrão. Para usar outro volume, altere `OLLAMA_VOLUME` no `.env`. Se o volume ainda não existir:

```powershell
docker volume create video_ollama_data
docker compose up -d ollama
docker compose exec ollama ollama pull qwen3:8b
```

## Uso recomendado da referência

- Uma única pessoa, rosto ocupando boa parte da imagem
- Rosto frontal ou levemente inclinado, olhos visíveis
- Luz uniforme e sem filtros fortes
- No mínimo 512 × 512 px

A fidelidade padrão de 85% costuma equilibrar identidade e liberdade de pose. Aumente se os traços variarem; reduza se o resultado copiar demais o enquadramento original.

## Comandos úteis

```powershell
docker compose ps
docker compose logs -f app comfyui
docker compose restart
docker compose down
```

ComfyUI fica disponível em `http://localhost:8188` e Ollama em `http://localhost:11434` para diagnóstico. Os dados do app persistem em `data/`; modelos ficam em `models/`.

## API

A documentação interativa está em `http://localhost:8080/docs`. Principais rotas:

- `POST /api/characters` — cadastra nome e foto
- `POST /api/generations` — enfileira uma criação
- `GET /api/generations/{id}` — acompanha o status
- `GET /api/health` — verifica Ollama, ComfyUI e InstantID

## Limites e privacidade

Use apenas fotos próprias ou com autorização. InstantID preserva principalmente o rosto; penteado, corpo, roupa e pose seguem o texto e podem variar. Para consistência extrema em muitas centenas de imagens, uma LoRA treinada com várias fotos é o próximo passo, mas não é necessária para este MVP.
