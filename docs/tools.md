# Tools

For anyone who wants to know exactly what the six tools take and return, and where their limits are.

Every tool only reads, and is marked read-only and closed-world for the client. Every description tells the model that message text is chat content: data, not instructions.

## The six tools

| Tool | Arguments (default; bounds) |
|---|---|
| `list_chats` | `query` (none), `kind` (none; `user`, `group` or `channel`), `include_archived` (false), `limit` (50; 1 to 200) |
| `get_chat` | `chat` |
| `list_topics` | `chat`, `limit` (100; 1 to 200) |
| `search_messages` | `query` (required), `chat` (none: all chats), `topic` (none; needs `chat`), `sender` (none; needs `chat`), `since` (none; a date), `until` (none; a date), `limit` (30; 1 to 100) |
| `get_messages` | `chat`, `topic` (none), `since` (none; a date and time), `until` (none; a date and time), `before_id` (none; at least 1), `limit` (50; 1 to 100) |
| `get_message_context` | `chat`, `message_id` (at least 1), `before` (10; 0 to 30), `after` (10; 0 to 30) |

`chat` is an id, an @username, a title or part of a title, of a chat in your dialog list; a part that fits several chats is refused with their ids and titles. `list_chats` matches `query` as a case-insensitive part of the title or username.

`get_chat` adds the member count and, for a channel, the id of its linked discussion group (`discussion_chat_id`).

`list_topics` and `topic` work only in forum groups; elsewhere the tool says the chat is not a forum.

`get_messages` returns the newest messages first and a `next_before_id`; pass it as `before_id` to read the next, older page.

`get_message_context` returns one message with up to `before` messages before it and `after` after it. In a forum, the context stays inside the message's topic.

## Dates

All dates and times are UTC.

- `search_messages`: `since` and `until` are whole UTC days, both included.
- `get_messages`: `since` and `until` are dates with times; `until` is not included. A time without a time zone is taken as UTC.
- `since` after `until` is refused.

A message sent just after your local midnight may belong to the previous UTC day if you live east of Greenwich.

## Search

Telegram's search matches the beginnings of words, not fragments: "market" finds "marketing" but not "supermarket". Every search result carries a note saying so, and the server tells the model to rephrase before concluding that nothing exists.

A search across all chats needs a non-empty `query`. `sender` and `topic` need `chat`.

`sender` is a user id or @username, and works reliably only for people you have a private chat with. A member of a large group or a channel is usually not found; the tool then says "Unknown sender" and suggests searching without `sender`. tgmcp never looks a username up on Telegram's servers.

## Size limits

| Limit | Value |
|---|---|
| One message's text | cut at 2 000 characters, with `text_truncated: true`; `get_message_context` with `before=0` and `after=0` returns it in full |
| `search_messages`, `get_messages`, `get_message_context` | about 30 000 bytes of JSON per result |
| `list_chats`, `list_topics` | about 25 000 bytes of JSON per result |
| One tool call | 30 seconds |

The budgets are in bytes, so Cyrillic text (two bytes a letter) fits about half as many characters.

When a result is cut:

- `search_messages` and `get_messages` say `truncated: true`; `get_messages` also gives a `next_before_id` to continue after the last message kept.
- `get_message_context` drops the messages farthest from the target first and says `truncated: true`.
- `list_chats` and `list_topics` return fewer items without a flag: narrow them with `query` or `kind`.

`get_messages` also says `truncated` when it stopped early in a long run of service messages (joins, pins); the cursor then continues below them. A search across all chats says `truncated` when Telegram returned a full page, also if hits from hidden chats were then dropped from it.

A local model gets at most 16 000 characters of each tool answer ([local-models.md](local-models.md#limits)).

## Hidden chats

Chats listed in `hidden_chats` are left out of every tool: not listed, not found by id, name or title, and dropped from searches across all chats. How to set them, and what hiding does not cover: [privacy.md](privacy.md#hidden-chats).

## Connection

Each running server connects to Telegram on the first tool call and disconnects after 10 minutes without one.
