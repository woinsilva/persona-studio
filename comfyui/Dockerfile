FROM video-comfyui:latest

USER root
RUN if [ ! -d /comfyui/custom_nodes/ComfyUI_InstantID ]; then \
      git clone --depth 1 https://github.com/cubiq/ComfyUI_InstantID.git /comfyui/custom_nodes/ComfyUI_InstantID; \
    fi \
    && pip install --no-cache-dir -r /comfyui/custom_nodes/ComfyUI_InstantID/requirements.txt

WORKDIR /comfyui
