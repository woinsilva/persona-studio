from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
REFERENCE_DIR = DATA_DIR / "references"
GENERATED_DIR = DATA_DIR / "generated"
DB_PATH = DATA_DIR / "persona.db"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188").rstrip("/")
CHECKPOINT = os.getenv("COMFY_CHECKPOINT", "RealVisXL_V4.0.safetensors")
INSTANTID_MODEL = os.getenv("INSTANTID_MODEL", "ip-adapter.bin")
INSTANTID_CONTROLNET = os.getenv("INSTANTID_CONTROLNET", "instantid-controlnet.safetensors")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_DIR.mkdir(exist_ok=True)
    GENERATED_DIR.mkdir(exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, reference_file TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY, character_id TEXT NOT NULL, prompt TEXT NOT NULL,
                enhanced_prompt TEXT, negative_prompt TEXT, status TEXT NOT NULL,
                error TEXT, output_file TEXT, seed INTEGER, width INTEGER, height INTEGER,
                fidelity REAL, created_at TEXT NOT NULL, completed_at TEXT,
                FOREIGN KEY(character_id) REFERENCES characters(id)
            );
            """
        )
        conn.execute(
            "UPDATE generations SET status='failed', error='Geração interrompida pela reinicialização do servidor.' WHERE status IN ('queued','running')"
        )
        references = conn.execute("SELECT reference_file FROM characters").fetchall()
    for row in references:
        reference_path = REFERENCE_DIR / row["reference_file"]
        face_path = REFERENCE_DIR / face_filename(row["reference_file"])
        if reference_path.exists() and not face_path.exists():
            create_face_reference(reference_path, face_path)


def face_filename(reference_file: str) -> str:
    path = Path(reference_file)
    return f"{path.stem}_face{path.suffix}"


def create_face_reference(source: Path, destination: Path) -> bool:
    """Create a face-focused identity image while keeping the original for pose."""
    image = cv2.imread(str(source))
    if image is None:
        return False
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=5, minSize=(36, 36))
    if len(faces) == 0:
        return False
    x, y, width, height = max(faces, key=lambda item: int(item[2]) * int(item[3]))
    center_x = x + width / 2
    center_y = y + height / 2
    side = max(width, height) * 3.0
    left = max(0, int(center_x - side / 2))
    top = max(0, int(center_y - side * 0.43))
    right = min(image.shape[1], int(center_x + side / 2))
    bottom = min(image.shape[0], int(center_y + side * 0.57))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return False
    crop = cv2.resize(crop, (1024, 1024), interpolation=cv2.INTER_LANCZOS4)
    return bool(cv2.imwrite(str(destination), crop, [cv2.IMWRITE_JPEG_QUALITY, 96]))


def row_to_character(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["reference_url"] = f"/media/references/{item.pop('reference_file')}"
    return item


def row_to_generation(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    filename = item.pop("output_file")
    item["output_url"] = f"/media/generated/{filename}" if filename else None
    return item


class GenerateRequest(BaseModel):
    character_id: str
    prompt: str = Field(min_length=3, max_length=2000)
    negative_prompt: str = Field(default="", max_length=1000)
    width: int = Field(default=1024, ge=512, le=1536)
    height: int = Field(default=1024, ge=512, le=1536)
    fidelity: float = Field(default=1.05, ge=0.35, le=1.35)
    steps: int = Field(default=28, ge=10, le=60)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    enhance_prompt: bool = True


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Persona Studio", version="1.0.0", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=DATA_DIR, check_dir=False), name="media")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=4) as client:
        async def probe(url: str) -> tuple[bool, Any]:
            try:
                response = await client.get(url)
                response.raise_for_status()
                return True, response.json()
            except Exception as exc:
                return False, str(exc)

        (ollama_ok, ollama_data), (comfy_ok, comfy_data) = await asyncio.gather(
            probe(f"{OLLAMA_URL}/api/tags"), probe(f"{COMFYUI_URL}/object_info")
        )

    available_models = [m.get("name", "") for m in ollama_data.get("models", [])] if ollama_ok else []
    missing_nodes: list[str] = []
    missing_models: list[str] = []
    if comfy_ok:
        for node in ("InstantIDModelLoader", "InstantIDFaceAnalysis", "ApplyInstantIDAdvanced"):
            if node not in comfy_data:
                missing_nodes.append(node)
        checks = (
            ("CheckpointLoaderSimple", "ckpt_name", CHECKPOINT),
            ("InstantIDModelLoader", "instantid_file", INSTANTID_MODEL),
            ("ControlNetLoader", "control_net_name", INSTANTID_CONTROLNET),
        )
        for node, field, expected in checks:
            try:
                available = comfy_data[node]["input"]["required"][field][0]
                if expected not in available:
                    missing_models.append(expected)
            except (KeyError, IndexError, TypeError):
                missing_models.append(expected)
    return {
        "ok": ollama_ok and comfy_ok and not missing_nodes and not missing_models,
        "ollama": {"online": ollama_ok, "model": OLLAMA_MODEL, "model_available": any(m.startswith(OLLAMA_MODEL) for m in available_models)},
        "comfyui": {"online": comfy_ok, "instantid_ready": comfy_ok and not missing_nodes and not missing_models, "missing_nodes": missing_nodes, "missing_models": missing_models},
    }


@app.get("/api/characters")
def list_characters() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM characters ORDER BY created_at DESC").fetchall()
    return [row_to_character(row) for row in rows]


@app.post("/api/characters", status_code=201)
async def create_character(name: str = Form(...), photo: UploadFile = File(...)) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 80:
        raise HTTPException(422, "Informe um nome com até 80 caracteres.")
    if photo.content_type not in {"image/jpeg", "image/jpg", "image/png", "image/webp", "application/octet-stream"}:
        raise HTTPException(415, "Use uma imagem JPG, PNG ou WebP.")
    raw = await photo.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "A foto deve ter no máximo 15 MB.")
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(raw))).convert("RGB")
        if min(image.size) < 256:
            raise ValueError("small")
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    except Exception as exc:
        raise HTTPException(422, "Não foi possível ler a foto ou ela é pequena demais.") from exc

    character_id = uuid.uuid4().hex
    filename = f"{character_id}.jpg"
    image.save(REFERENCE_DIR / filename, "JPEG", quality=94, optimize=True)
    create_face_reference(REFERENCE_DIR / filename, REFERENCE_DIR / face_filename(filename))
    with db() as conn:
        conn.execute(
            "INSERT INTO characters(id,name,reference_file,created_at) VALUES(?,?,?,?)",
            (character_id, clean_name, filename, utcnow()),
        )
        row = conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    return row_to_character(row)


@app.delete("/api/characters/{character_id}")
def delete_character(character_id: str) -> None:
    with db() as conn:
        row = conn.execute("SELECT reference_file FROM characters WHERE id=?", (character_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Personagem não encontrado.")
        has_generations = conn.execute("SELECT 1 FROM generations WHERE character_id=? LIMIT 1", (character_id,)).fetchone()
        if has_generations:
            raise HTTPException(409, "Este personagem possui imagens no histórico.")
        conn.execute("DELETE FROM characters WHERE id=?", (character_id,))
    (REFERENCE_DIR / row["reference_file"]).unlink(missing_ok=True)


@app.get("/api/generations")
def list_generations(limit: int = 40) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT g.*, c.name AS character_name FROM generations g JOIN characters c ON c.id=g.character_id ORDER BY g.created_at DESC LIMIT ?",
            (min(max(limit, 1), 100),),
        ).fetchall()
    return [row_to_generation(row) for row in rows]


@app.get("/api/generations/{generation_id}")
def get_generation(generation_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute(
            "SELECT g.*, c.name AS character_name FROM generations g JOIN characters c ON c.id=g.character_id WHERE g.id=?",
            (generation_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Geração não encontrada.")
    return row_to_generation(row)


@app.post("/api/generations", status_code=202)
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    with db() as conn:
        character = conn.execute("SELECT * FROM characters WHERE id=?", (payload.character_id,)).fetchone()
        if not character:
            raise HTTPException(404, "Personagem não encontrado.")
        generation_id = uuid.uuid4().hex
        seed = payload.seed if payload.seed is not None else random.randint(0, 2**32 - 1)
        conn.execute(
            """INSERT INTO generations(id,character_id,prompt,negative_prompt,status,seed,width,height,fidelity,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (generation_id, payload.character_id, payload.prompt, payload.negative_prompt, "queued", seed, payload.width, payload.height, payload.fidelity, utcnow()),
        )
    asyncio.create_task(run_generation(generation_id, dict(character), payload, seed))
    return {"id": generation_id, "status": "queued"}


async def enhance_prompt(prompt: str) -> str:
    instruction = f"""Transforme a descrição abaixo em um único prompt EM INGLÊS para fotografia realista.
Preserve exatamente pessoas, ação, ambiente, roupa e intenção. Acrescente somente câmera, lente, luz,
composição e textura natural de pele. Não mencione nomes, identidade, InstantID, IA ou explicações.
Responda em JSON no formato {{"prompt":"..."}}.

DESCRIÇÃO: {prompt}"""
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": instruction,
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "keep_alive": 0,
                    "options": {"temperature": 0.35, "num_predict": 180},
                },
            )
            response.raise_for_status()
            content = response.json().get("response", "")
        result = json.loads(content)["prompt"].strip()
        return result or prompt
    except Exception:
        # Prompt enhancement is optional and must never block image generation.
        return f"photorealistic professional photography, natural skin texture, {prompt}"


def build_workflow(reference_name: str, pose_name: str, prompt: str, negative: str, req: GenerateRequest, seed: int, prefix: str) -> dict[str, Any]:
    negative_base = "illustration, painting, drawing, 3d render, CGI, anime, doll, plastic skin, deformed face, asymmetrical eyes, extra fingers, blurry, low resolution, oversaturated, (watermark:1.5), (logo:1.4), stock photo, getty images, gettyimages, signature, text"
    negative_text = f"{negative_base}, {negative.strip()}" if negative.strip() else negative_base
    width = req.width - (req.width % 64)
    height = req.height - (req.height % 64)
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text, "clip": ["1", 1]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": reference_name}},
        "13": {"class_type": "LoadImage", "inputs": {"image": pose_name}},
        "5": {"class_type": "InstantIDModelLoader", "inputs": {"instantid_file": INSTANTID_MODEL}},
        "6": {"class_type": "InstantIDFaceAnalysis", "inputs": {"provider": "CUDA"}},
        "7": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": INSTANTID_CONTROLNET}},
        "8": {"class_type": "ApplyInstantIDAdvanced", "inputs": {
            "instantid": ["5", 0], "insightface": ["6", 0], "control_net": ["7", 0], "image": ["4", 0],
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
            "ip_weight": req.fidelity, "cn_strength": min(req.fidelity * 0.9, 1.1), "start_at": 0.0,
            "end_at": 1.0, "noise": 0.35, "combine_embeds": "average", "image_kps": ["13", 0]
        }},
        "9": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "10": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": req.steps, "cfg": 4.5, "sampler_name": "dpmpp_2m_sde",
            "scheduler": "karras", "denoise": 1.0, "model": ["8", 0], "positive": ["8", 1],
            "negative": ["8", 2], "latent_image": ["9", 0]
        }},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["1", 2]}},
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["11", 0]}},
    }


async def run_generation(generation_id: str, character: dict[str, Any], req: GenerateRequest, seed: int) -> None:
    try:
        with db() as conn:
            conn.execute("UPDATE generations SET status='running' WHERE id=?", (generation_id,))
        prompt = await enhance_prompt(req.prompt) if req.enhance_prompt else req.prompt
        reference_path = REFERENCE_DIR / character["reference_file"]
        face_path = REFERENCE_DIR / face_filename(character["reference_file"])
        if not face_path.exists():
            create_face_reference(reference_path, face_path)
        identity_path = face_path if face_path.exists() else reference_path
        async with httpx.AsyncClient(timeout=120) as client:
            with identity_path.open("rb") as handle:
                identity_upload = await client.post(
                    f"{COMFYUI_URL}/upload/image",
                    files={"image": (f"persona-{generation_id}-face.jpg", handle, "image/jpeg")},
                    data={"overwrite": "true", "type": "input"},
                )
            identity_upload.raise_for_status()
            with reference_path.open("rb") as handle:
                pose_upload = await client.post(
                    f"{COMFYUI_URL}/upload/image",
                    files={"image": (f"persona-{generation_id}-pose.jpg", handle, "image/jpeg")},
                    data={"overwrite": "true", "type": "input"},
                )
            pose_upload.raise_for_status()
            reference_name = identity_upload.json()["name"]
            pose_name = pose_upload.json()["name"]
            workflow = build_workflow(reference_name, pose_name, prompt, req.negative_prompt, req, seed, f"persona/{generation_id}")
            queued = await client.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
            if queued.status_code >= 400:
                detail = queued.text[:1000]
                raise RuntimeError(f"O ComfyUI recusou o workflow: {detail}")
            prompt_id = queued.json()["prompt_id"]

        output_info = await wait_for_output(prompt_id)
        params = {"filename": output_info["filename"], "subfolder": output_info.get("subfolder", ""), "type": output_info.get("type", "output")}
        async with httpx.AsyncClient(timeout=90) as client:
            result = await client.get(f"{COMFYUI_URL}/view", params=params)
            result.raise_for_status()
        filename = f"{generation_id}.png"
        (GENERATED_DIR / filename).write_bytes(result.content)
        with db() as conn:
            conn.execute(
                "UPDATE generations SET status='completed', enhanced_prompt=?, output_file=?, completed_at=? WHERE id=?",
                (prompt, filename, utcnow(), generation_id),
            )
    except Exception as exc:
        message = re.sub(r"\s+", " ", str(exc)).strip()[:1200]
        if not message:
            message = f"{type(exc).__name__}: o serviço não respondeu dentro do tempo esperado."
        with db() as conn:
            conn.execute("UPDATE generations SET status='failed', error=?, completed_at=? WHERE id=?", (message, utcnow(), generation_id))


async def wait_for_output(prompt_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        for _ in range(450):
            history = await client.get(f"{COMFYUI_URL}/history/{prompt_id}")
            history.raise_for_status()
            job = history.json().get(prompt_id)
            if job:
                status = job.get("status", {})
                if status.get("status_str") == "error" or (status.get("completed") is False and status.get("messages")):
                    raise RuntimeError("A geração falhou no ComfyUI. Consulte docker compose logs comfyui para detalhes.")
                for node in job.get("outputs", {}).values():
                    images = node.get("images", [])
                    if images:
                        return images[0]
            await asyncio.sleep(2)
    raise TimeoutError("A geração excedeu o limite de 15 minutos.")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str = ""):
    requested = BASE_DIR / "static" / path
    if path and requested.is_file() and requested.resolve().is_relative_to((BASE_DIR / "static").resolve()):
        return FileResponse(requested)
    return FileResponse(BASE_DIR / "static" / "index.html")
