"""Explicitly deployed custom inference/training recipe. Importing this module starts no GPU work.

Deploy only after recording licensed weights, an approved dataset and an experiment budget.
The model registry is an operator-maintained file on the model Volume, never a caller-supplied path.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import modal

app = modal.App("kleos-models")
weights = modal.Volume.from_name("kleos-model-weights", create_if_missing=True)
checkpoints = modal.Volume.from_name("kleos-training-checkpoints", create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch==2.9.0", "transformers==4.57.1", "datasets==4.3.0", "accelerate==1.11.0", "httpx==0.28.1",
)


def approved_model(model_id, root=Path("/models")):
    registry = json.loads((root / "registry.json").read_text())
    entry = registry.get(model_id)
    if not entry or entry.get("status") != "promoted":
        raise ValueError("This model has not been promoted in the operator registry")
    path = (root / entry["path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_dir():
        raise ValueError("Invalid model weight path")
    return path


def download_input(item, allowed_bucket, max_bytes=2_000_000):
    import httpx
    parsed = urlsplit(item["url"])
    hosts = {f"{allowed_bucket}.s3.amazonaws.com",
             f"{allowed_bucket}.s3.{os.environ.get('AWS_REGION', 'us-east-1')}.amazonaws.com"}
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.port not in {None, 443} or (
            parsed.username or parsed.password):
        raise ValueError("Input must be a scoped URL from the configured artifact bucket")
    with httpx.stream("GET", item["url"], timeout=30, follow_redirects=False, trust_env=False) as response:
        response.raise_for_status()
        content = bytearray()
        for block in response.iter_bytes():
            content.extend(block)
            if len(content) > max_bytes:
                raise ValueError("Model input exceeds its declared limit")
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise ValueError("Artifact checksum mismatch")
    return bytes(content)


@app.function(image=image, gpu="L4", timeout=600, max_containers=2,
              volumes={"/models": weights}, secrets=[modal.Secret.from_name("kleos-artifact-access")])
def infer(request: dict):
    started = time.monotonic()
    from transformers import pipeline
    model_path = approved_model(request["modelId"])
    bucket = os.environ["KLEOS_ARTIFACT_BUCKET"]
    inputs = request.get("inputs", [])
    if not 1 <= len(inputs) <= 10:
        raise ValueError("Inference requires one to ten bounded inputs")
    prompt = "\n\n".join(download_input(item, bucket).decode("utf-8") for item in inputs)
    if len(prompt) > 40000:
        raise ValueError("Inference input is too large")
    model = pipeline("text-generation", model=str(model_path), device_map="auto", trust_remote_code=False)
    result = model(prompt, max_new_tokens=512, do_sample=False, return_full_text=False)
    return {"jobId": request["jobId"], "modelId": request["modelId"], "status": "candidate",
            "output": result[0]["generated_text"], "physicalAcceptance": False,
            "executionSeconds": time.monotonic() - started, "gpuType": "L4"}


@app.function(image=image, gpu="L4", timeout=3600, max_containers=1,
              volumes={"/models": weights, "/checkpoints": checkpoints},
              secrets=[modal.Secret.from_name("kleos-artifact-access")])
def train(request: dict):
    started = time.monotonic()
    from datasets import Dataset as HFDataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainerCallback,
        TrainingArguments,
    )
    # The operator provisions the licensed base weights; callers cannot trigger arbitrary model downloads.
    base = (Path("/models") / request["baseModelPath"]).resolve()
    if not base.is_relative_to(Path("/models")) or not base.is_dir():
        raise ValueError("Licensed base weights must already be provisioned")
    experiment = str(request["modelId"])
    if not experiment or any(c not in "0123456789abcdef-" for c in experiment):
        raise ValueError("Use the registered model UUID")
    out = Path("/checkpoints") / experiment
    out.mkdir(parents=True, exist_ok=True)
    raw = download_input(request["dataset"], os.environ["KLEOS_ARTIFACT_BUCKET"], max_bytes=20_000_000)
    records = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if not records or len(records) > 10000 or any(record.get("split") != "training" for record in records):
        raise ValueError("Training input must contain 1–10,000 training-only examples")
    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    data = HFDataset.from_list([{"text": record["text"]} for record in records]).map(
        lambda batch: tokenizer(batch["text"], truncation=True, max_length=1024),
        batched=True, remove_columns=["text"])
    model = AutoModelForCausalLM.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    arguments = TrainingArguments(
        output_dir=str(out), max_steps=min(500, int(request.get("maxSteps", 100))),
        per_device_train_batch_size=1, gradient_accumulation_steps=4,
        save_steps=20, save_total_limit=2, logging_steps=10, report_to=[], fp16=True,
    )
    ceiling = int(request.get("maxGpuSeconds", 0))
    if not 1 <= ceiling <= 3600:
        raise ValueError("A runtime ceiling between 1 and 3600 seconds is required")
    class RuntimeCeiling(TrainerCallback):
        def __init__(self):
            self.started = started

        def on_step_end(self, args, state, control, **kwargs):
            if time.monotonic() - self.started >= ceiling:
                control.should_save = True
                control.should_training_stop = True
            return control

        def on_save(self, args, state, control, **kwargs):
            checkpoints.commit()
            return control

    trainer = Trainer(model=model, args=arguments, train_dataset=data,
                      data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
                      callbacks=[RuntimeCeiling()])
    prior = sorted(out.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
    trainer.train(resume_from_checkpoint=str(prior[-1]) if prior else None)
    trainer.save_model(str(out / "candidate"))
    tokenizer.save_pretrained(str(out / "candidate"))
    checkpoints.commit()
    return {"modelId": experiment, "status": "candidate", "checkpointPath": f"{experiment}/candidate",
            "jobId": request["jobId"], "requiresEvaluation": True, "datasetSha256": request["dataset"]["sha256"],
            "executionSeconds": time.monotonic() - started, "gpuType": "L4"}
