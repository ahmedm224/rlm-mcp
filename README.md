# RLM MCP Server

[![Beta](https://img.shields.io/badge/status-beta-yellow)](https://github.com/ahmedm224/rlm-mcp)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Paper](https://img.shields.io/badge/arXiv-2512.24601-b31b1b.svg)](https://arxiv.org/abs/2512.24601)

**Analyze 10GB+ files with Claude Code** — no API keys required.

An MCP server implementing [MIT's Recursive Language Models](https://arxiv.org/abs/2512.24601) that lets Claude Code analyze files too large for its context window.

```
┌─────────────────────────────────────────────────────────────┐
│  "Find all errors in this 5GB log file"                     │
│                                                             │
│  Claude → writes Python → RLM executes → returns results    │
│                                                             │
│  Result: 78% fewer tokens, same accuracy                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Start

**1. Install:**
```bash
pip install rlm-mcp
```

**2. Configure Claude Code** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "rlm": {
      "command": "rlm-mcp"
    }
  }
}
```

**3. Use it:**
```
Load /var/log/syslog and find all kernel errors
```

That's it. Claude automatically uses RLM for large file analysis.

---

## Why RLM?

| Problem | Traditional | RLM Solution |
|---------|-------------|--------------|
| 10GB log file | ❌ Doesn't fit in context | ✅ Loads externally, queries via Python |
| Token usage | 📈 ~12,500 tokens | 📉 ~2,700 tokens (78% less) |
| Complex analysis | ❌ Limited to grep patterns | ✅ Full Python (regex, stats, aggregation) |

### Real Benchmark

Testing on a 300KB system log with Claude Code Opus 4.5:

```
┌───────────┬──────────────┬───────────────┬─────────┐
│  Method   │ Input Tokens │ Output Tokens │  Total  │
├───────────┼──────────────┼───────────────┼─────────┤
│ Grep/Read │ ~10,000      │ ~2,500        │ ~12,500 │
│ RLM       │ ~1,500       │ ~1,200        │ ~2,700  │
└───────────┴──────────────┴───────────────┴─────────┘

Both methods found identical results.
RLM used 78% fewer tokens.
```

---

## The Science

Based on **[Recursive Language Models](https://arxiv.org/abs/2512.24601)** from MIT CSAIL:

> *"We propose treating the long context as an 'external environment' to be interacted with via a Python REPL..."*
> — Alex L. Zhang, Tim Kraska, Omar Khattab (MIT), 2025

### Paper Results

| Benchmark | Traditional | RLM |
|-----------|-------------|-----|
| S-NIAH (8M tokens) | 39.3% | **96.0%** |
| OOLONG QA | 36.2% | **56.7%** |

---

## How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Claude Code   │────▶│   RLM Server    │────▶│  Python REPL    │
│   (The Brain)   │◀────│   (MCP)         │◀────│  (Execution)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘

1. You ask: "Find errors in this huge log"
2. Claude loads file via rlm_load_file()
3. Claude writes Python: re.findall(r'ERROR.*', context)
4. RLM executes on full file (outside Claude's context)
5. Only results return to Claude
6. Claude answers with findings
```

**Key insight:** Claude is the brain, RLM is the hands. No API keys needed — uses your Claude Code subscription.

---

## Available Tools

| Tool | Description |
|------|-------------|
| `rlm_load_file` | Load a massive file |
| `rlm_load_multiple_files` | Load multiple files as dict |
| `rlm_execute_code` | Run Python on loaded content |
| `rlm_get_variable` | Get a variable's value |
| `rlm_session_info` | Check session state |
| `rlm_reset_session` | Clear session memory |

---

## When to Use

```
┌─────────────────────────────┬────────────────────┐
│          Use Case           │    Recommended     │
├─────────────────────────────┼────────────────────┤
│ Small files (<50KB)         │ Direct read        │
│ Single pattern search       │ Grep               │
│ Large files (>200KB)        │ ✅ RLM             │
│ Complex analysis/statistics │ ✅ RLM             │
│ Multi-pattern correlation   │ ✅ RLM             │
│ Aggregation/counting        │ ✅ RLM             │
│ Cross-file analysis         │ ✅ RLM             │
└─────────────────────────────┴────────────────────┘
```

---

## Example Session

```python
# Load a large log
>>> rlm_load_file("/var/log/app.log")
File loaded: 2,847,392 chars

# Search for errors
>>> rlm_execute_code("""
import re
errors = re.findall(r'ERROR.*', context)
print(f"Found {len(errors)} errors")
""")
Found 156 errors

# Analyze patterns
>>> rlm_execute_code("""
from collections import Counter
types = re.findall(r'ERROR.*?\] (\w+)', context)
print(Counter(types).most_common(5))
""")
[('Connection', 67), ('Database', 43), ('Timeout', 28)]
```

---

## Safety

- **30s timeout** — Runaway code auto-killed
- **Process isolation** — Uses multiprocessing
- **Output truncation** — Prevents memory issues

---

## Requirements

- Python 3.10+
- Claude Code with MCP support
- No API keys needed

---

## Links

- **GitHub:** https://github.com/ahmedm224/rlm-mcp
- **Paper:** https://arxiv.org/abs/2512.24601
- **Issues:** https://github.com/ahmedm224/rlm-mcp/issues

---

## Citation

```bibtex
@article{zhang2025recursive,
  title={Recursive Language Models},
  author={Zhang, Alex L. and Kraska, Tim and Khattab, Omar},
  journal={arXiv preprint arXiv:2512.24601},
  year={2025}
}
```

---

## License

MIT © Ahmed Ali
