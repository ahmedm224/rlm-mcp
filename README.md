# RLM MCP Server

> **Implementation of [Recursive Language Models (RLM)](https://arxiv.org/abs/2512.24601) from MIT**
>
> *"We propose treating the long context as an 'external environment' to be interacted with via a Python REPL... recursively calling sub-LLMs to analyze relevant sections."*
>
> — Diao et al., MIT CSAIL, 2025

An MCP server that brings the power of Recursive Language Models to Claude Code, enabling analysis of massive documents that exceed context windows.

**No API keys required** — works with Claude Code subscriptions.

---

## The Paper

This project implements the core ideas from:

**[Recursive Language Models](https://arxiv.org/abs/2512.24601)**
*Shizhe Diao, Tianyu Liu, Rui Pan, Xiang Xiang Liu, Jipeng Zhang, Tao Wang, Pengfei Liu*
MIT CSAIL • December 2025

### Key Insight

Traditional LLMs struggle with massive contexts (millions of tokens). The RLM paper proposes a paradigm shift:

> Instead of stuffing everything into the context window, treat the data as an **external environment** that the LLM explores programmatically via a Python REPL.

```
┌─────────────────────────────────────────────────────────────────┐
│  Traditional Approach          │  RLM Approach                  │
├─────────────────────────────────────────────────────────────────┤
│  [LLM] ← entire 10GB file      │  [LLM] → write Python code     │
│         (doesn't fit!)         │         ↓                      │
│                                │  [REPL] → execute on 10GB file │
│                                │         ↓                      │
│                                │  [LLM] ← only relevant results │
└─────────────────────────────────────────────────────────────────┘
```

### Results from the Paper

| Benchmark | Traditional | RLM | Improvement |
|-----------|-------------|-----|-------------|
| S-NIAH (8M tokens) | 39.3% | **96.0%** | +144% |
| OOLONG QA | 36.2% | **56.7%** | +57% |
| Cost (vs full context) | 100% | **~15%** | -85% |

---

## Installation

```bash
pip install rlm-mcp
```

## Setup for Claude Code

Add to your Claude Code settings:

| OS | Settings Location |
|----|-------------------|
| macOS/Linux | `~/.claude/settings.json` |
| Windows | `%USERPROFILE%\.claude\settings.json` |

```json
{
  "mcpServers": {
    "rlm": {
      "command": "rlm-mcp"
    }
  }
}
```

Restart Claude Code after adding the configuration.

---

## Usage

Once configured, ask Claude Code to analyze large files naturally:

```
Load /var/log/syslog and find all kernel errors from the past week
```

```
Analyze all Python files in /src and find functions without docstrings
```

```
Search this 2GB JSON export for all records where status is "failed"
```

Claude automatically:
1. Loads files into the REPL environment
2. Writes Python code to analyze them
3. Iterates based on results
4. Returns findings

---

## How It Works

This implementation adapts the RLM paper for Claude Code:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Claude Code   │────▶│   RLM MCP       │────▶│  Python REPL    │
│   (The Brain)   │◀────│   Server        │◀────│  (The Hands)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘

1. You ask: "Find all errors in this massive log"
2. Claude calls: rlm_load_file("/path/to/huge.log")
3. Claude writes: Python code to search (regex, string ops)
4. REPL executes: Code runs on the full file (outside context)
5. Claude sees: Only the relevant matches
6. Claude answers: "Found 47 errors, here are the patterns..."
```

**Key difference from the paper**: Instead of the RLM making its own sub-LLM calls, Claude Code itself acts as the orchestrating LLM. This means:
- No API keys needed (uses your Claude Code subscription)
- Claude's full reasoning capabilities guide the analysis
- Works within Claude Code's existing permission model

---

## Available Tools

| Tool | Description |
|------|-------------|
| `rlm_load_file` | Load a single file into the REPL |
| `rlm_load_multiple_files` | Load multiple files as a dict |
| `rlm_execute_code` | Run Python code on loaded content |
| `rlm_get_variable` | Retrieve a variable's value |
| `rlm_session_info` | Check current session state |
| `rlm_reset_session` | Clear session to free memory |

---

## Example Session

```python
# Claude loads a large log file
>>> rlm_load_file("/var/log/app.log")
File loaded: 2,847,392 chars (~711K tokens)

# Claude searches for errors
>>> rlm_execute_code("""
import re
errors = re.findall(r'ERROR.*', context)
print(f"Found {len(errors)} errors")
for e in errors[:5]:
    print(e)
""")
Found 156 errors
ERROR [2025-01-20 10:23:45] Connection timeout
ERROR [2025-01-20 10:24:01] Database query failed
...

# Claude analyzes patterns
>>> rlm_execute_code("""
from collections import Counter
error_types = re.findall(r'ERROR.*?\] (\w+)', context)
for err, count in Counter(error_types).most_common(5):
    print(f"{err}: {count}")
""")
Connection: 67
Database: 43
Timeout: 28
...
```

---

## Safety Features

- **30-second timeout**: Runaway code is automatically killed
- **Process isolation**: Uses multiprocessing for hard termination
- **Output truncation**: Large outputs are truncated to prevent memory issues
- **Session isolation**: Multiple analyses can run in parallel

---

## When to Use RLM

### Real-World Comparison: Grep/Read vs RLM -

We tested both approaches on the same task: **analyzing a 300KB system log to find critical events**. using Claude Code running Opus 4.5

#### Token Usage

```
┌──────────────────────────┬────────────────────────────────────┬──────────────────────────────┐
│          Aspect          │        Method 1: Grep/Read         │        Method 2: RLM         │
├──────────────────────────┼────────────────────────────────────┼──────────────────────────────┤
│ Tool Calls               │ 5 calls (4 Grep + 1 Read)          │ 5 calls (1 Load + 4 Execute) │
├──────────────────────────┼────────────────────────────────────┼──────────────────────────────┤
│ Data Returned to Context │ ~8,000+ tokens                     │ ~800 tokens                  │
├──────────────────────────┼────────────────────────────────────┼──────────────────────────────┤
│ File Content in Context  │ Partial (grep results + 100 lines) │ None (processed externally)  │
├──────────────────────────┼────────────────────────────────────┼──────────────────────────────┤
│ Output Size              │ Large (full matching lines)        │ Small (only print() output)  │
└──────────────────────────┴────────────────────────────────────┴──────────────────────────────┘
```

#### Token Consumption

```
┌───────────┬──────────────┬───────────────┬─────────┐
│  Method   │ Input Tokens │ Output Tokens │  Total  │
├───────────┼──────────────┼───────────────┼─────────┤
│ Grep/Read │ ~10,000      │ ~2,500        │ ~12,500 │
├───────────┼──────────────┼───────────────┼─────────┤
│ RLM       │ ~1,500       │ ~1,200        │ ~2,700  │
└───────────┴──────────────┴───────────────┴─────────┘
```

**RLM used 78% fewer tokens** while achieving identical results.

#### Accuracy (Both Methods)

```
┌────────────────────────┬───────────┬──────────┐
│         Metric         │ Grep/Read │   RLM    │
├────────────────────────┼───────────┼──────────┤
│ Kernel Events Found    │ 6 unique  │ 6 unique │
├────────────────────────┼───────────┼──────────┤
│ Application Crashes    │ 12        │ 12       │
├────────────────────────┼───────────┼──────────┤
│ Critical System Events │ 1         │ 1        │
├────────────────────────┼───────────┼──────────┤
│ Top 10 Match           │ ✓         │ ✓        │
└────────────────────────┴───────────┴──────────┘
```

Both methods found the same results — but RLM did it with **78% fewer tokens**.

### Decision Guide

```
┌─────────────────────────────┬────────────────────┐
│          Use Case           │ Recommended Method │
├─────────────────────────────┼────────────────────┤
│ Small files (<50KB)         │ Grep/Read          │
├─────────────────────────────┼────────────────────┤
│ Single pattern search       │ Grep/Read          │
├─────────────────────────────┼────────────────────┤
│ Large files (>200KB)        │ RLM                │
├─────────────────────────────┼────────────────────┤
│ Complex analysis/statistics │ RLM                │
├─────────────────────────────┼────────────────────┤
│ Multi-pattern correlation   │ RLM                │
├─────────────────────────────┼────────────────────┤
│ Aggregation/counting        │ RLM                │
└─────────────────────────────┴────────────────────┘
```

### Pros and Cons

| | Grep/Read | RLM |
|---|-----------|-----|
| **Pros** | Native tools, no dependencies | Massive token savings |
| | Fast for simple searches | Full file access |
| | Good for single-pattern queries | Complex analysis possible |
| | | Statistics/aggregation easy |
| **Cons** | Token-heavy for large files | Requires Python knowledge |
| | Limited to regex patterns | Extra setup (load file first) |
| | Hard to correlate across lines | Overkill for simple searches |
| | No aggregation/counting | |

---

## Requirements

- Python 3.10+
- Claude Code with MCP support
- No API keys required

---

## Development

```bash
git clone https://github.com/ahmedm224/rlm-mcp
cd rlm-mcp
pip install -e .
```

---

## Citation

If you use this in research, please cite the original paper:

```bibtex
@article{diao2025recursive,
  title={Recursive Language Models},
  author={Diao, Shizhe and Liu, Tianyu and Pan, Rui and Liu, Xiang Xiang and Zhang, Jipeng and Wang, Tao and Liu, Pengfei},
  journal={arXiv preprint arXiv:2512.24601},
  year={2025}
}
```

---

## License

MIT

---

## Acknowledgments

- The [Recursive Language Models paper](https://arxiv.org/abs/2512.24601) by MIT CSAIL for the foundational ideas
- Anthropic for Claude Code and the MCP protocol
