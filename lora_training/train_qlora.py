# ==============================================================================
# SIS Chatbot -- QLoRA Fine-Tune (Llama 3.1 8B Instruct), retrain pass.
# Same config as SIS_QLoRA_Training.ipynb, pointed at the glossary-reinforced
# training set so service-code facts (ISD=0154 etc.) get enough repetition to
# actually stick instead of the model guessing a nearby digit.
# ==============================================================================

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig

TRAIN_PATH = "/content/train_augmented.jsonl"
VAL_PATH = "/content/validation.jsonl"

BASE_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
OUTPUT_DIR = "/content/sis-qlora-adapter-v2"

LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.05
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj",
                   "gate_proj", "up_proj", "down_proj"]

LEARNING_RATE = 2e-4
NUM_EPOCHS = 2
PER_DEVICE_BATCH = 2
GRAD_ACCUM = 16
MAX_SEQ_LEN = 1024
LR_SCHEDULER = "cosine"
WARMUP_STEPS = 10
SEED = 42

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,
)

tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    quantization_config=bnb_config,
    device_map="auto",
    torch_dtype=torch.bfloat16,
)
model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
model.config.use_cache = False

lora_config = LoraConfig(
    r=LORA_R,
    lora_alpha=LORA_ALPHA,
    lora_dropout=LORA_DROPOUT,
    target_modules=TARGET_MODULES,
    bias="none",
    task_type="CAUSAL_LM",
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

dataset = load_dataset("json", data_files={"train": TRAIN_PATH, "validation": VAL_PATH})
print(dataset)
# Deliberately NOT pre-rendered to a flat "text" field here. Flattening with
# apply_chat_template() and handing SFTTrainer plain text makes it compute
# loss over every token -- system prompt and the officer's own question
# included, which dilutes the gradient on the thing we actually want it to
# learn: the assistant's answer. Passing the raw "messages" column lets
# SFTTrainer apply the chat template AND mask the loss to assistant turns
# only (completion_only_loss below) -- check that flag name against your
# installed trl version (`python -c "from trl import SFTConfig; help(SFTConfig)"`)
# if this errors; older/newer trl releases have called it assistant_only_loss.

sft_config = SFTConfig(
    output_dir=OUTPUT_DIR,
    num_train_epochs=NUM_EPOCHS,
    per_device_train_batch_size=PER_DEVICE_BATCH,
    per_device_eval_batch_size=PER_DEVICE_BATCH,
    gradient_accumulation_steps=GRAD_ACCUM,
    gradient_checkpointing=True,
    gradient_checkpointing_kwargs={"use_reentrant": False},
    learning_rate=LEARNING_RATE,
    lr_scheduler_type=LR_SCHEDULER,
    warmup_steps=WARMUP_STEPS,
    optim="paged_adamw_8bit",
    max_length=MAX_SEQ_LEN,
    packing=False,
    completion_only_loss=True,  # mask loss to assistant turns only
    logging_steps=10,
    eval_strategy="steps",
    eval_steps=50,
    save_strategy="steps",
    save_steps=50,
    save_total_limit=2,
    load_best_model_at_end=True,
    metric_for_best_model="eval_loss",
    bf16=True,
    seed=SEED,
    report_to="none",
)

trainer = SFTTrainer(
    model=model,
    args=sft_config,
    train_dataset=dataset["train"],
    eval_dataset=dataset["validation"],
    processing_class=tokenizer,
)

trainer.train()
trainer.save_model(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)
print("Done. Adapter saved to", OUTPUT_DIR)
