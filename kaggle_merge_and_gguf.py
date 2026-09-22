# ==============================================================================
# SIS QLoRA -> GGUF Q4_K_M, for Kaggle
#
# Run as ONE cell. Uses llama.cpp's own convert_lora_to_gguf.py +
# llama-export-lora to merge the adapter into the base at the GGUF level --
# no hand-rolled tensor merge, no giant intermediate merged-safetensors copy.
#
# Disk layout (Kaggle /kaggle/working save quota is 20GB -- large
# intermediates live in /kaggle/temp instead, only the final GGUF is saved):
#   /kaggle/temp/     <- base safetensors, base F16 GGUF, adapter GGUF, merged F16 GGUF
#   /kaggle/working/  <- ONLY the final sis-llama-Q4_K_M.gguf
#
# Before running:
#   1. Attach a Kaggle Dataset containing adapter_config.json + adapter_model.safetensors
#   2. Add HF_TOKEN as a Kaggle Secret (Add-ons -> Secrets), enable it for this notebook.
#   3. Save Version -> Save & Run All (don't rely on the interactive session staying open)
# ==============================================================================

import os
import shutil

ADAPTER_DIR = "/kaggle/input/datasets/mayurkaarthicsv/sis-qlora-adapter"

BASE_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
TEMP = "/kaggle/temp"
WORKING = "/kaggle/working"
BASE_LOCAL_DIR = f"{TEMP}/base_model_raw"
LLAMA_CPP_DIR = f"{TEMP}/llama.cpp"
BASE_GGUF_F16 = f"{TEMP}/base-f16.gguf"
ADAPTER_GGUF = f"{TEMP}/adapter-lora.gguf"
MERGED_GGUF_F16 = f"{TEMP}/merged-f16.gguf"
FINAL_Q4 = f"{WORKING}/sis-llama-Q4_K_M.gguf"

os.makedirs(TEMP, exist_ok=True)


def run(cmd):
    print("+", cmd)
    rc = os.system(cmd)
    if rc != 0:
        raise RuntimeError(f"command failed ({rc}): {cmd}")


# ------------------------------------------------------------------------------
# STEP 0: login + deps
# ------------------------------------------------------------------------------
from kaggle_secrets import UserSecretsClient
HF_TOKEN = UserSecretsClient().get_secret("HF_TOKEN")

run("pip install -q -U huggingface_hub")
from huggingface_hub import login, snapshot_download
login(token=HF_TOKEN)

# ------------------------------------------------------------------------------
# STEP 1: download the base model checkpoint to /kaggle/temp (disk, not RAM)
# ------------------------------------------------------------------------------
print("Downloading base model checkpoint...")
snapshot_download(
    repo_id=BASE_MODEL,
    local_dir=BASE_LOCAL_DIR,
    allow_patterns=["*.safetensors", "*.json", "*.model", "tokenizer*"],
)

# ------------------------------------------------------------------------------
# STEP 2: get llama.cpp
# ------------------------------------------------------------------------------
run(f"git clone --depth 1 https://github.com/ggerganov/llama.cpp {LLAMA_CPP_DIR}")
run(f"pip install -q -r {LLAMA_CPP_DIR}/requirements.txt")
run(f"cmake -S {LLAMA_CPP_DIR} -B {LLAMA_CPP_DIR}/build -DGGML_CUDA=OFF")
run(f"cmake --build {LLAMA_CPP_DIR}/build --config Release -j --target llama-export-lora")

# ------------------------------------------------------------------------------
# STEP 3: base -> F16 GGUF
# ------------------------------------------------------------------------------
print("Converting base model to GGUF (f16)...")
run(f"python {LLAMA_CPP_DIR}/convert_hf_to_gguf.py {BASE_LOCAL_DIR} "
    f"--outfile {BASE_GGUF_F16} --outtype f16")

shutil.rmtree(BASE_LOCAL_DIR, ignore_errors=True)  # free disk, no longer needed

# ------------------------------------------------------------------------------
# STEP 4: adapter safetensors -> LoRA GGUF
# ------------------------------------------------------------------------------
print("Converting LoRA adapter to GGUF...")
run(f"python {LLAMA_CPP_DIR}/convert_lora_to_gguf.py {ADAPTER_DIR} "
    f"--base {BASE_MODEL} "
    f"--outfile {ADAPTER_GGUF} --outtype f16")

# ------------------------------------------------------------------------------
# STEP 5: merge LoRA into the base GGUF (llama.cpp's own tool -- no hand-rolled
# tensor math, no dtype guesswork)
# ------------------------------------------------------------------------------
print("Merging LoRA into base GGUF...")
run(f"{LLAMA_CPP_DIR}/build/bin/llama-export-lora "
    f"-m {BASE_GGUF_F16} --lora {ADAPTER_GGUF} -o {MERGED_GGUF_F16}")

os.remove(BASE_GGUF_F16)
os.remove(ADAPTER_GGUF)

# ------------------------------------------------------------------------------
# STEP 6: quantize to Q4_K_M -- final file goes to /kaggle/working
# ------------------------------------------------------------------------------
run(f"cmake --build {LLAMA_CPP_DIR}/build --config Release -j --target llama-quantize")

print("Quantizing to Q4_K_M...")
run(f"{LLAMA_CPP_DIR}/build/bin/llama-quantize {MERGED_GGUF_F16} {FINAL_Q4} Q4_K_M")

os.remove(MERGED_GGUF_F16)

print("Done. Final file:", FINAL_Q4)
print("Size:", os.path.getsize(FINAL_Q4) / 1e9, "GB")
