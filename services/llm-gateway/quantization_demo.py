#!/usr/bin/env python3
"""
quantization_demo.py -- standalone quantization demo.

NOT part of the running llm-gateway FastAPI service, not imported by it, and
not installed in the Docker image. Run it manually on your own machine when
you want a hands-on feel for what "quantization" actually buys you.

What it does:
  1. Loads a small HuggingFace causal LM (distilgpt2 by default) once in
     full fp32 precision, prints its memory footprint, times a short
     generation, and prints the output.
  2. Attempts the same load in 8-bit via bitsandbytes
     (BitsAndBytesConfig(load_in_8bit=True)). If bitsandbytes isn't
     installed, or no CUDA GPU is available (the common case on a laptop),
     it prints a clear message and skips that half instead of crashing.
  3. Prints a summary comparing the two memory footprints.

See README.md's "Quantization demo" section
(README.md#quantization-demo-appendix-run-manually) for what this
demonstrates and how it connects to the `ollama` provider in this same
service (Ollama's default GGUF models are themselves quantized -- this
script shows *why* that matters).

Usage:
    pip install torch transformers accelerate
    python quantization_demo.py

    # optional, only does something useful with a CUDA GPU present:
    pip install bitsandbytes
    python quantization_demo.py --model Qwen/Qwen2.5-0.5B-Instruct
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

DEFAULT_MODEL = "distilgpt2"
DEFAULT_PROMPT = "The most important thing about running LLMs efficiently is"


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def model_memory_footprint(model) -> int:
    """Sum of all parameter + buffer byte sizes -- a reasonable proxy for the
    model's resident memory footprint (does not include activations or KV
    cache, which scale with sequence length rather than model size)."""
    total = 0
    for p in model.parameters():
        total += p.nelement() * p.element_size()
    for b in model.buffers():
        total += b.nelement() * b.element_size()
    return total


def run_fp32(model_name: str, prompt: str, max_new_tokens: int) -> int:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"\n=== fp32 baseline: {model_name} ===")
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=torch.float32)
    model.eval()
    load_s = time.time() - t0

    footprint = model_memory_footprint(model)
    print(f"Load time:        {load_s:.2f}s")
    print(f"Memory footprint: {_human_bytes(footprint)}  ({footprint:,} bytes)")

    inputs = tokenizer(prompt, return_tensors="pt")
    t0 = time.time()
    with torch.no_grad():
        output_ids = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    gen_s = time.time() - t0
    text = tokenizer.decode(output_ids[0], skip_special_tokens=True)
    print(f"Generation time:  {gen_s:.2f}s")
    print(f"Output: {text!r}")

    del model
    return footprint


def run_int8(model_name: str, prompt: str, max_new_tokens: int) -> Optional[int]:
    print(f"\n=== 8-bit (bitsandbytes) attempt: {model_name} ===")
    try:
        import torch  # noqa: F401
        import bitsandbytes  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        print(f"SKIPPED: bitsandbytes (or a dependency) is not installed: {exc}")
        print("Install with: pip install bitsandbytes")
        print("Note: most bitsandbytes releases require an NVIDIA GPU for the")
        print("8-bit LLM.int8() inference path. On a CPU-only machine this demo")
        print("will not run the 8-bit half at all -- that's expected, not a bug")
        print("in this script. See README.md#quantization-demo-appendix-run-manually.")
        return None

    if not torch.cuda.is_available():
        print("SKIPPED: bitsandbytes is installed, but no CUDA GPU was detected.")
        print("8-bit quantization via bitsandbytes' LLM.int8() requires a GPU in")
        print("most released versions. This is exactly the gap that Ollama's GGUF")
        print("quantization (used by the `ollama` provider in this same service)")
        print("solves for CPU-only / laptop-friendly local inference.")
        print("See README.md#quantization-demo-appendix-run-manually.")
        return None

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    quant_config = BitsAndBytesConfig(load_in_8bit=True)

    t0 = time.time()
    try:
        model = AutoModelForCausalLM.from_pretrained(
            model_name, quantization_config=quant_config, device_map="auto"
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"SKIPPED: 8-bit load failed on this machine: {exc}")
        return None
    model.eval()
    load_s = time.time() - t0

    footprint = model_memory_footprint(model)
    print(f"Load time:        {load_s:.2f}s")
    print(f"Memory footprint: {_human_bytes(footprint)}  ({footprint:,} bytes)")

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    t0 = time.time()
    with torch.no_grad():
        output_ids = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    gen_s = time.time() - t0
    text = tokenizer.decode(output_ids[0], skip_special_tokens=True)
    print(f"Generation time:  {gen_s:.2f}s")
    print(f"Output: {text!r}")

    del model
    return footprint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"HuggingFace model id (default: {DEFAULT_MODEL})")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-new-tokens", type=int, default=40)
    args = parser.parse_args()

    try:
        fp32_footprint = run_fp32(args.model, args.prompt, args.max_new_tokens)
    except ImportError as exc:
        print(f"ERROR: missing dependency for the fp32 baseline: {exc}", file=sys.stderr)
        print("Install with: pip install torch transformers", file=sys.stderr)
        return 1

    int8_footprint = run_int8(args.model, args.prompt, args.max_new_tokens)

    print("\n=== Summary ===")
    print(f"fp32 footprint:  {_human_bytes(fp32_footprint)}")
    if int8_footprint:
        ratio = fp32_footprint / int8_footprint
        print(f"8-bit footprint: {_human_bytes(int8_footprint)}  ({ratio:.2f}x smaller)")
    else:
        print("8-bit footprint: N/A (skipped -- see message above)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
