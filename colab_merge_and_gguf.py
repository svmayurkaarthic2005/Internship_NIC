# ==============================================================================
# SIS QLoRA -> GGUF Q4_K_M, for Google Colab (Pro)
#
# Uses llama.cpp's own convert_lora_to_gguf.py + llama-export-lora to merge
# the adapter into the base at the GGUF level -- no hand-rolled tensor merge.
#
# Before running:
#   1. Runtime -> Change runtime type -> GPU (A100/L4/T4, whatever Pro gives you)
#   2. Left sidebar -> key icon (Secrets) -> add HF_TOKEN, enable "Notebook access"
#      (the adapter itself comes from the sis-qlora-adapter Kaggle dataset via the
#      kaggle API -- see the separate download cell that runs before this one)
# ==============================================================================

import os

ADAPTER_DIR = "/content/drive/MyDrive/sis-qlora-adapter"   # must contain adapter_config.json + adapter_model.safetensors

BASE_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
BASE_LOCAL_DIR = "/content/base_model_raw"
LLAMA_CPP_DIR = "/content/llama.cpp"
BASE_GGUF_F16 = "/content/base-f16.gguf"
ADAPTER_GGUF = "/content/adapter-lora.gguf"
MERGED_GGUF_F16 = "/content/merged-f16.gguf"
FINAL_Q4 = "/content/sis-llama-Q4_K_M.gguf"


def run(cmd):
    print("+", cmd)
    rc = os.system(cmd)
    if rc != 0:
        raise RuntimeError(f"command failed ({rc}): {cmd}")


# ------------------------------------------------------------------------------
# STEP 0: login + deps
# ------------------------------------------------------------------------------
from google.colab import userdata
HF_TOKEN = userdata.get("HF_TOKEN")

run("pip install -q -U huggingface_hub")
from huggingface_hub import login, snapshot_download
login(token=HF_TOKEN)

# ------------------------------------------------------------------------------
# STEP 1: download the base model checkpoint to disk (not RAM)
# ------------------------------------------------------------------------------
if not os.path.exists(os.path.join(BASE_LOCAL_DIR, "model.safetensors.index.json")):
    print("Downloading base model checkpoint...")
    snapshot_download(
        repo_id=BASE_MODEL,
        local_dir=BASE_LOCAL_DIR,
        allow_patterns=["*.safetensors", "*.json", "*.model", "tokenizer*"],
    )
else:
    print("Base model already on disk, skipping download.")

# ------------------------------------------------------------------------------
# STEP 2: get llama.cpp
# ------------------------------------------------------------------------------
if not os.path.exists(LLAMA_CPP_DIR):
    run(f"git clone --depth 1 https://github.com/ggerganov/llama.cpp {LLAMA_CPP_DIR}")
    run(f"pip install -q -r {LLAMA_CPP_DIR}/requirements.txt")
else:
    print("llama.cpp already cloned, skipping.")

if not os.path.exists(f"{LLAMA_CPP_DIR}/build/bin/llama-export-lora"):
    run(f"cmake -S {LLAMA_CPP_DIR} -B {LLAMA_CPP_DIR}/build -DGGML_CUDA=OFF")
    run(f"cmake --build {LLAMA_CPP_DIR}/build --config Release -j --target llama-export-lora")
else:
    print("llama-export-lora already built, skipping.")

# ------------------------------------------------------------------------------
# STEP 3: base -> F16 GGUF
# ------------------------------------------------------------------------------
if not os.path.exists(BASE_GGUF_F16):
    print("Converting base model to GGUF (f16)...")
    run(f"python {LLAMA_CPP_DIR}/convert_hf_to_gguf.py {BASE_LOCAL_DIR} "
        f"--outfile {BASE_GGUF_F16} --outtype f16")
else:
    print("Base F16 GGUF already exists, skipping.")

# ------------------------------------------------------------------------------
# STEP 4: adapter safetensors -> LoRA GGUF
# ------------------------------------------------------------------------------
if not os.path.exists(ADAPTER_GGUF):
    print("Converting LoRA adapter to GGUF...")
    run(f"python {LLAMA_CPP_DIR}/convert_lora_to_gguf.py {ADAPTER_DIR} "
        f"--base {BASE_MODEL} "
        f"--outfile {ADAPTER_GGUF} --outtype f16")
else:
    print("Adapter GGUF already exists, skipping.")

# ------------------------------------------------------------------------------
# STEP 5: merge LoRA into the base GGUF
# ------------------------------------------------------------------------------
if not os.path.exists(MERGED_GGUF_F16):
    print("Merging LoRA into base GGUF...")
    run(f"{LLAMA_CPP_DIR}/build/bin/llama-export-lora "
        f"-m {BASE_GGUF_F16} --lora {ADAPTER_GGUF} -o {MERGED_GGUF_F16}")
else:
    print("Merged F16 GGUF already exists, skipping.")

# ------------------------------------------------------------------------------
# STEP 6: quantize to Q4_K_M
# ------------------------------------------------------------------------------
if not os.path.exists(f"{LLAMA_CPP_DIR}/build/bin/llama-quantize"):
    run(f"cmake --build {LLAMA_CPP_DIR}/build --config Release -j --target llama-quantize")

if not os.path.exists(FINAL_Q4):
    print("Quantizing to Q4_K_M...")
    run(f"{LLAMA_CPP_DIR}/build/bin/llama-quantize {MERGED_GGUF_F16} {FINAL_Q4} Q4_K_M")
else:
    print("Final Q4_K_M already exists, skipping.")

print("Done. Final file:", FINAL_Q4)
print("Size:", os.path.getsize(FINAL_Q4) / 1e9, "GB")
