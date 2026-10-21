# Local models

For anyone who wants a model on their own PC to answer questions about their chats, so that the questions, the messages the tools read and the answers never go to an AI company.

`tgmcp` below means `.venv\Scripts\tgmcp` in cmd.exe or `.venv/Scripts/tgmcp` in Git Bash/PowerShell, run from the cloned folder.

tgmcp itself still fetches messages from Telegram's servers, as always. What "local" checks, and what it cannot see, is in [privacy.md](privacy.md#local-models).

## What it costs

A local model is much smaller than a cloud model: its answers are simpler and it makes more mistakes (see "Limits"). A graphics card with about 8 to 12 GB of memory helps; on the processor alone it works, slowly.

Both ways below need `tgmcp login` once before ([setup.md](setup.md)).

## LM Studio (a chat window)

Not yet verified live: these steps follow LM Studio's documentation.

1. Install LM Studio from https://lmstudio.ai.
2. Search for a model from "Which model" below, download it and load it.
3. When loading, set the context length (how much text the model can hold at once, counted in tokens) to at least 8192 (16384 is better if it fits); a shorter context cuts off tool answers.
4. Open the Program tab, then Install, then Edit mcp.json, and add this inside `mcpServers`, with the full path of your cloned folder (`cd` in cmd.exe, `pwd` in Git Bash or PowerShell) and every backslash doubled:

   ```json
   "telegram": {
     "command": "C:\\full\\path\\telegram-mcp\\.venv\\Scripts\\tgmcp.exe",
     "args": ["serve"]
   }
   ```

   If `mcpServers` already holds other servers, put a comma after the neighbouring entry. Save the file.

5. In the Developer tab, keep "Serve on local network" off.
6. Start a new chat and ask. LM Studio asks you to confirm each tool call.

## Ollama and `tgmcp ask` (one question in a terminal)

1. Install Ollama from https://ollama.com.
2. Download a model (about 6 to 9 GB):

   ```
   ollama pull gemma4:12b
   ```

3. Ask, from the cloned folder:

   ```
   tgmcp ask "What did Nora say about the boots?" --model gemma4:12b
   ```

Only the answer is printed. In Git Bash, a `!` inside double quotes is read as a history command; put such a question in single quotes.

### Options of `tgmcp ask`

| Option | What it does |
|---|---|
| `--model NAME` | the model, as the backend lists it. Without it: `local_model` from `config.json` or `TGMCP_LOCAL_MODEL`, else the first model the backend lists |
| `--backend ollama` or `lmstudio` | Ollama (the default) or LM Studio's local server (start it in LM Studio's Developer tab first) |
| `--url URL` | the backend's address; it must be on this computer. Defaults: `http://127.0.0.1:11434` (Ollama), `http://127.0.0.1:1234` (LM Studio) |
| `--think` | let the model think first (slower, sometimes better); Ollama only |
| `--max-steps N` | how many times the model may be called (default 8) |
| `--show-calls` | also print each tool call and the size of its answer, for example `-> search_messages(query="boots") 2311 chars`; `cut` means the answer was shortened |

To make a model the default, add `"local_model": "gemma4:12b"` to `config.json` ([privacy.md](privacy.md#where-things-are-stored)).

With Ollama, tgmcp asks for a context of 16384 tokens and temperature 0. Each model call may take up to 120 seconds.

If the model does not finish within the steps, or makes two broken tool calls in a row, tgmcp prints `(unfinished after N steps)` and what the model had so far (or the tool calls it made). Ask a narrower question: one chat, a shorter period.

## Which model works best: `tgmcp bench`

`tgmcp bench` asks a model 35 fixed questions about an invented account built into tgmcp (no login, none of your chats) and checks the answers with fixed rules, not with another model.

```
tgmcp bench --model gemma4:12b
```

Options: `--model` (required), `--backend`, `--url` and `--think` as for `ask`; `--set smoke` for five questions only; `--repeat N` to ask each question N times (small models do not always answer the same way); `--json FILE` to save every question, tool call, answer and score.

### Reading the table

The first line names the run: `model=… backend=… ctx=… think=… repeat=…` (with LM Studio, `ctx=app think=n/a`, because LM Studio sets both itself). Compare only runs with the same `ctx`, `think` and `repeat`.

Then one row per kind of question that was run (`lookup`, `history`, `paging`, `context`, `topic`, `hidden`, `refusal`, `multilingual`, `search`) and a `total` row. The columns:

| Column | Meaning |
|---|---|
| `n` | number of runs |
| `tools` | the right tools were called, in order |
| `args` | with the right arguments |
| `answer` | the answer contains the expected facts |
| `no_inv` | the answer names no chat id, cited message id or chat title it had not seen |
| `steps` | average tool calls per run |
| `s` | average seconds per run (the first includes loading the model) |

Higher percentages are better. The language of the answer is not scored. The last line says what `no_inv` does not cover: "no_invention checks chat ids, message ids cited as ids, and the seed's chat titles; dates and other names are not checked".

## Which model

Candidates that fit a 12 GB graphics card, all in Ollama's library:

| Model | Download |
|---|---|
| Gemma 4, 12 B | `ollama pull gemma4:12b` |
| Qwen 3.5, 9 B | `ollama pull qwen3.5:9b` |
| Ministral 3, 14 B | `ollama pull ministral-3:14b` |
| Qwen 3, 14 B | `ollama pull qwen3:14b` |

Measured so far: `gemma4:12b` scored 100 % on `--set smoke` (21.10.2026, ctx=16384, think=off, repeat=1, RTX 4070 Ti). The full set has not been run on these models yet. Run `tgmcp bench` on the ones that fit your PC and keep the one with the best `total` row. tgmcp refuses a model that Ollama says cannot call tools.

## Limits

- `tgmcp ask` answers one question and forgets it: put everything into one question.
- Answers are weaker than a large cloud model's, most of all on long summaries.
- A tool answer longer than 16 000 characters is cut before it reaches the model, with a line asking for a smaller `limit`.
- The bench does not check dates or retellings, except where a question expects a specific fact.
- Telegram's search needs the word in the chat's language; small models asked in one language do not always search in another.
