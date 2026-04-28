# Models Directory

Place model weight files here. These files are **gitignored** due to their size; obtain or build them separately and place them in this directory before running the application.

## Expected files

| File | Description |
|------|-------------|
| `bestsmall.onnx` | Primary PPE detection model (ONNX format). Configure path via `yolo.model_path` in `cleanroom_config.yaml`. |
| `yolov11s.json` | DeGirum zoo descriptor for Hailo inference. Required when `hailo.enable: true`. |
| `HailoDetectionYolo.py` | Hailo post-processor loaded by DeGirum at runtime. |
| `hailo_labels.json` | Class label map for Hailo model outputs. |

## Obtaining model files

1. **ONNX / PyTorch fallback** – Train with [Ultralytics](https://docs.ultralytics.com/) and export to ONNX:
   ```bash
   yolo export model=best.pt format=onnx
   ```
   Place the resulting `.onnx` file here and set `yolo.model_path` accordingly.

2. **Hailo HEF** – Compile the ONNX model to a `.hef` file using the [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo) toolchain, then deploy via DeGirum.  
   See `hailo.model_name` and `hailo.zoo_path` in `cleanroom_config.yaml`.

3. **Disable Hailo acceleration** – Set `hailo.enable: false` in `cleanroom_config.yaml` to fall back to the Ultralytics / ONNX path without any Hailo hardware.
