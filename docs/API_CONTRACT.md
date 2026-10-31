# StutterTriage Core API Contract (read-only extraction)

Source: `stuttertriage/` 包 + `StutterTriage.py` 入口 (8078 lines, PyQt6 + pyqtgraph + numpy).
Target audience: an engineer who will **rewrite only the UI layer** and import these classes from new modules.
Module-level constant *values* are quoted from their definition sites (lines 486–560, 640, 1245–1270, 2116, 7318–7380); everything else comes from the four instructed ranges (1357–1472, 1473–1545, 1546–2575, 2576–3730).
Nothing in this document is invented: every identifier, literal and default is quoted verbatim from the source. Ambiguities are marked `[UNCLEAR:line]`.

Legend: `ms` = milliseconds of wall time per frame, `MB/s` = 1048576 bytes/second, `KiB/MiB` not used — the code uses 1048576 for MB and 4096 for a page.

---

## 0. Class / function index for the documented ranges

| Line | Object | Kind | Thread ownership |
|---|---|---|---|
| 1361 | `Event` | data model | plain data, cross-thread by value |
| 1377 | `HardwareSnapshot` | data model | plain data, cross-thread by value |
| 1437 | `FrameRecord` | data model | plain data, cross-thread by value |
| 1460 | `DiffRow` | data model | plain data, UI thread |
| 1477 | `EtwSessionManager` | service | callable from any thread; spawns `logman` subprocesses |
| 1550 | `HardwareEngine(threading.Thread)` | producer thread | own thread; only `stop()` is UI-safe |
| 2083 | `_presentmon_search_roots()` | helper | pure |
| 2116 | `_PM_CAP_CACHE = {}` | module global (defined inside range) | shared dict, unsynchronized |
| 2119 | `detect_presentmon_capabilities(exe_path)` | helper | blocking subprocess (`--help`, 20 s timeout) |
| 2156 | `find_presentmon_binary(explicit=None)` | helper | blocking filesystem glob |
| 2205 | `PresentMonBridge(threading.Thread)` | producer thread | own thread; only `stop()` is UI-safe |
| 2580 | `BaselineTracker` | stateful calculator | UI thread only (no locks) |
| 2772 | `FrameStore` | in-memory DB | **single-thread writer**, no locks |
| 3272 | `SnapshotDiffInspector` | stateless calculator | call from UI thread; pure apart from the `store`/`FrameStore` reads |
| 3643 | `classify_gc_lock` | static pure function | any thread |
| 3702 | `banner_verdict` | static pure function | any thread |

Producer→consumer channel: **`queue.Queue`/`BoundedQueue` of `(tag, payload)` tuples**.
Tags published inside the ranges, verbatim: `"hw"`, `"event"` (from `HardwareEngine`), `"frame"`, `"status"` (from `PresentMonBridge`).
The existing UI consumes them in `StutterTriageWindow._drain_queue` (line 4226): `"frame"`→`store.add_frame`, `"hw"`→`store.add_snapshot`, `"event"`→`store.add_event`, `"status"`→stashed in `self._pending_status`.

---

## 1. `Event` (line 1361–1374)

```python
class Event:
    __slots__ = ("ts", "kind", "text", "advice", "severity", "symbol", "frame")
    def __init__(self, ts, kind, text, advice="", severity="WARN", symbol="o")
```

| Field | Type | Unit / domain | Meaning |
|---|---|---|---|
| `ts` | `float` | Unix epoch seconds (`time.time()`) | Event instant. Used by `FrameStore.add_event` for ±1.5 s frame binding. |
| `kind` | `str` | one of the exact literals below | Causality category / UI grouping key. |
| `text` | `str` | free text (Chinese) | Message body shown in the event table. |
| `advice` | `str` | free text (Chinese) | Remediation hint; `""` when unknown. |
| `severity` | `str` | `"INFO"` \| `"WARN"` \| `"DANGER"` | Default `"WARN"`. Drives table colouring. |
| `symbol` | `str` | pyqtgraph scatter symbol, must be a member of `PG_SYMBOLS` (line 558) | Default `"o"`. Values actually emitted inside the ranges: `"t"` (Alt+Tab), `"v"` (freq drop / GPU throttle), `"s"` (CPU0 jam), `"x"` (core migration), `"o"` (VRAM spill), `"p"` (page-in), `"d"` (shader / disk I/O). |
| `frame` | `FrameRecord` \| `None` | — | Reverse reference, set **only** by `FrameStore.add_event` when a frame lies within 1.5 s. Initialised `None`. Used by the overview decimator to keep dotted frames. |

**`kind` literals emitted inside the ranges (exact strings):**
`"系统"`, `"降频"`, `"CPU0拥堵"`, `"调度迁移"`, `"溢出"`, `"内存"`, `"着色器"`, `"磁盘I/O"`, `"GPU降频"`.
Outside the ranges the UI also creates `"手动标记"` (line 6652, severity `"WARN"`, symbol `"star"`) — listed for completeness, not part of the core contract.

`__slots__` ⇒ no ad-hoc attributes may be attached by the new UI.

---

## 2. `HardwareSnapshot` (line 1377–1434)

```python
class HardwareSnapshot:
    __slots__ = ("seq","ts","adapters","procs","cpu_usage","cpu_freq","cpu_total",
                 "cpu0_usage","dominant_core","core_variance","mem_avail_mb","mem_commit",
                 "mem_limit","page_in","page_out","pagein_top","gpu_eng","cache",
                 "cache_new_files","cache_new_mb","vram_dedicated","vram_total",
                 "vram_nonlocal","vram_local","io_read_mb","io_other_mb","io_write_mb",
                 "io_top","io_by_name","net_rx_mb","net_tx_mb","net_adapters",
                 "gpu_clock","gpu_clock_max","gpu_temp")
    def __init__(self, seq=0)
```

All 35 fields are set in `__init__`, so every attribute always exists. `__slots__` ⇒ immutable shape, no extra attributes.

| # | Field | Type | Unit | Meaning / producer |
|---|---|---|---|---|
| 1 | `seq` | `int` | — | Monotonic **1-based** snapshot id (`HardwareEngine._seq` pre-incremented in `assemble`, line 1638). `FrameRecord.hw_seq` points at it; `FrameStore.snapshot_for(seq)` resolves it. |
| 2 | `ts` | `float` | epoch s | Set to `time.time()` at construction and re-set to `now` at the top of `assemble` (1641). |
| 3 | `adapters` | `dict[int, dict]` | — | Key = 64-bit LUID (`(high32<<32)|low32` from `_RE_LUID`). Value = `{"name": str, "total": int bytes, "dedicated": float bytes, "shared": float bytes}`. `name` falls back to `"GPU (LUID %08X)"`; `total == 0` means **capacity unknown**. Only adapters with `dedicated > 0` or `shared > 0` (or unknown total with usage ≥ 1 MiB) are listed. |
| 4 | `procs` | `list[dict]` | — | Per-PID GPU memory, sorted by `(-local, -nonlocal)`, **truncated to 20**. Dict keys: `pid:int`, `name:str` (`NameTable`), `local`, `dedicated`, `shared`, `nonlocal` (all bytes, `float`), `total` = `local + nonlocal` (bytes). Processes with `dedicated < 1 MiB and local < 1 MiB` are dropped. |
| 5 | `cpu_usage` | `list[float]` | % per logical core | Length = `cpu_count()`; index = core number. Instance names tried: `"0,%d"` then `"%d"`. Negative values clamped to 0. |
| 6 | `cpu_freq` | `list[float]` | MHz per core | `base_max_mhz[i] * (% Processor Performance / 100)`; if the counter is missing/≤0 it falls back to the per-core max, and if `per_core_max_mhz()` failed the engine uses `[3500]*n` (line 1565). |
| 7 | `cpu_total` | `float` | % | `\_Total` instance of `\Processor Information(*)\% Processor Time`; if missing, arithmetic mean of `cpu_usage`. |
| 8 | `cpu0_usage` | `float` | % | `cpu_usage[0]` (0.0 when empty). |
| 9 | `dominant_core` | `int` | core index | `int(np.argmax(cpu_usage))`; 0 when empty. |
| 10 | `core_variance` | `float` | %² (variance of percents) | `float(np.var(cpu_usage))`. Threshold used by event detection: `> 450`. |
| 11 | `mem_avail_mb` | `float` | MB | First value of `\Memory\Available MBytes` (single-instance counter; `next(iter(...))`, line 2071). |
| 12 | `mem_commit` | `float` | **bytes** | First value of `\Memory\Committed Bytes` (raw PDH double, **not** converted; the `_mb` suffix is absent). |
| 13 | `mem_limit` | `float` | **bytes** | First value of `\Memory\Commit Limit`. |
| 14 | `page_in` | `float` | MB/s | `pages_to_mb_per_s(Pages Input/sec)` = raw × 4096 / 1048576. |
| 15 | `page_out` | `float` | MB/s | same for `Pages Output/sec`. |
| 16 | `pagein_top` | `list[tuple[str, float]]` | (process name, MB/s) | Up to **5** entries, descending rate, `PageFaultCount` deltas over `\Process` enumeration (working set ≥ 20 MiB filter). Populated **only** when `snap.page_in > 2.0` **and** ≥ 1.5 s since the previous page-in walk (lines 1656–1658); otherwise it is `[]` (stale from a previous round or empty). Rate × 4096 / 1048576. |
| 17 | `gpu_eng` | `dict[str, float]` | % | Engine-type → **summed** utilization over all `engtype_*` instances with value > 0 (e.g. `"3D"`, `"Copy"`). Used as `max(s.gpu_eng.values())` for the "3D load" gate. |
| 18 | `cache` | `dict` | mixed | Direct return value of `CacheWatcher.scan()`: per shader-cache-root tag `{tag: {"files": int, "mb": float, "new_files": int, "new_mb": float}}` **plus** 4 meta keys: `"_scan_ms": float`, `"_tot_new_files": int`, `"_tot_new_mb": float`, `"_timeout": bool`. Tags come from `CACHE_DIRS` (line 1245): `"D3D"`, `"AMD"`, `"AMD-Sys"`, `"NVIDIA"`, `"NV-GL"`, `"NV-Sys"`, `"Intel"` — only directories that exist on the machine appear. |
| 19 | `cache_new_files` | `int` | files / round | `cache["_tot_new_files"]` (0 when the key is absent). |
| 20 | `cache_new_mb` | `float` | MB / round | `cache["_tot_new_mb"]`. |
| 21 | `vram_dedicated` | `float` | bytes | Sum over adapters of `\GPU Adapter Memory\Dedicated Usage`. |
| 22 | `vram_total` | `float` | bytes | Adapter capacity from DXGI; **`0.0` = capacity unknown** (explicit contract, line 1413/1884). Display layer must render `—`, never a percentage. |
| 23 | `vram_nonlocal` | `float` | bytes | Sum of per-process `\GPU Process Memory\Non Local Usage` (**spill to system memory**). |
| 24 | `vram_local` | `float` | bytes | Sum of per-process `Local Usage`. |
| 25 | `io_read_mb` | `float` | MB/s | Read rate of **the single busiest process** (not the machine total). |
| 26 | `io_other_mb` | `float` | MB/s | `IO Other Bytes/sec` **of that same busiest process**. |
| 27 | `io_write_mb` | `float` | MB/s | Write rate of that same busiest process. |
| 28 | `io_top` | `list[tuple[str, float]]` | (name, MB/s read) | ≤ 3 entries, descending read rate, only entries with read > 0. |
| 29 | `io_by_name` | `dict[str, tuple[float,float,float]]` | (read MB/s, other MB/s, write MB/s) | Keys are **lower-cased process names with the `#n` suffix stripped** (`chrome#1234` → `chrome`), aggregating all instances. Top-K by read+write+other sum, K = `HardwareEngine._io_keep = 20`. Lookup helper used by the diagnostic card: full name → name minus `.exe` → first 15 chars (PDH truncation). |
| 30 | `net_rx_mb` | `float` | MB/s | Total receive over adapters **excluding** `NET_EXCLUDE` keywords. |
| 31 | `net_tx_mb` | `float` | MB/s | Total transmit, same exclusion. |
| 32 | `net_adapters` | `dict[str, tuple[float,float]]` | (rx MB/s, tx MB/s) | Per adapter instance name; adapters with both rates ≤ 0 are omitted. |
| 33 | `gpu_clock` | `int` \| `None` | MHz | `None` = probe unavailable (no ADL/NVML). **Never** coerce to 0 for display: 0 would fabricate a −100 % throttle. Set only when `GpuClockProbe.read()` returned a truthy clock. |
| 34 | `gpu_clock_max` | `int` | MHz | Session peak (`HardwareEngine._clk_peak`), 0 until the first successful read. |
| 35 | `gpu_temp` | `float` \| `None` | °C | `None` = unavailable. |

NA semantics: the engine never writes `None` for numeric counters, only `0.0`/`[]`/`{}`. The two exceptions that carry "unknown" as `None` are `gpu_clock` and `gpu_temp`. `vram_total == 0` and `adapters[*]["total"] == 0` are the "unknown capacity" sentinels. `pagein_top == []` means "no expensive page-fault walk was made this round", not "no faults".

---

## 3. `FrameRecord` (line 1437–1457)

```python
class FrameRecord:
    __slots__ = ("idx","t","wall_ts","app","pid","frametime","fps","gpu_busy","cpu_busy",
                 "gpu_busy_ratio","cpu_busy_ratio","hw_seq","events")
    def __init__(self, idx, t, wall_ts, app, pid, frametime,
                 gpu_busy=0.0, cpu_busy=0.0, hw_seq=-1)
```

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `idx` | `int` | — | 1-based frame counter within the bridge run (resets per `PresentMonBridge` instance). |
| `t` | `float` | seconds since capture start | Chart X axis. Derived from QPC when available (`(t_ms - _qpc_base)/1000`), else `now - t0`. |
| `wall_ts` | `float` | epoch seconds | Wall-clock time, aligned by the "sliding minimum offset" method. **This is the key used for snapshot binding and event binding**, never `t`. |
| `app` | `str` | — | `Application` CSV column, or the literal `"<unknown>"` when empty; simulation uses `"GameSimulation.exe"`. |
| `pid` | `int` | — | `ProcessID`, `0` if not all-digits. |
| `frametime` | `float` | ms | `max(0.1, float(frametime))` — hard floor of **0.1 ms**. |
| `fps` | `float` | frames/s | **Derived: `fps = 1000.0 / frametime`** (exactly this, no smoothing). |
| `gpu_busy` | `float` | ms | `max(0.0, ...)`, from `MsGPUBusy`/`msGPUActive`/`GPUBusy`; `0.0` also means "no data" (NA). |
| `cpu_busy` | `float` | ms | `max(0.0, ...)`, from `MsCPUBusy`/`CPUBusy`. |
| `gpu_busy_ratio` | `float` | fraction 0…1 | `gpu_busy / frametime` (0.0 if frametime ≤ 0). Multiply by 100 for percent. |
| `cpu_busy_ratio` | `float` | fraction 0…1 | `cpu_busy / frametime`. |
| `hw_seq` | `int` | — | Seq of the bound `HardwareSnapshot`; set by `FrameStore.add_frame` (constructor default `-1` = unbound). |
| `events` | `list[Event]` | — | Started empty; appended by `FrameStore.add_event` (≤ 1.5 s proximity). |

`frametime ↔ fps` are strictly reciprocal and `fps` is computed once at construction; changing `frametime` afterwards does not update `fps`. `__slots__` ⇒ no extra attributes.

---

## 4. `DiffRow` (line 1460–1470)

```python
class DiffRow:
    __slots__ = ("dim", "cur", "base", "delta", "verdict", "severity")
    def __init__(self, dim, cur, base, delta, verdict, severity="NORMAL")
```

| Field | Type | Meaning |
|---|---|---|
| `dim` | `str` | Dimension label — the card's first column. **Not a key**: `banner_verdict` matches it with `str.startswith`. See §9 for the 11 exact strings. |
| `cur` | `str` | Pre-formatted current value at the anomaly instant (row 3/7/8/9 include units and context). |
| `base` | `str` | Pre-formatted baseline string, or `"—"` when undecidable. |
| `delta` | `str` | Pre-formatted delta (`fmt_delta(...)` or an inline format), or `"—"`. |
| `verdict` | `str` | Emoji-prefixed verdict prose (`🔴/🟠/🟢/⚪/📊`). |
| `severity` | `str` | `"NORMAL"` \| `"WARN"` \| `"DANGER"`. Default `"NORMAL"`. |

Severity literals: **`DiffRow`/`banner_verdict` use `NORMAL`/`WARN`/`DANGER`; `Event` uses `INFO`/`WARN`/`DANGER`.** The two vocabularies are not interchangeable.

All four data models are plain, `__slots__`-based value objects: safe to hand across threads **by reference** as long as only one thread mutates a given instance (`FrameStore` is the only mutator in the current design).

---

## 5. `EtwSessionManager` (line 1477–1543)

```python
class EtwSessionManager:
    def __init__(self)                      # sets self.session_name
    @staticmethod
    def list_sessions()                     # -> list[str]
    @staticmethod
    def stop_session(name)                  # -> bool
    def cleanup_stale(self, skip=None)      # -> list[str]
    def dispose(self)                       # -> None
    def alive(self)                         # -> bool
```

Public attribute: `session_name: str` = `PM_SESSION_PREFIX + uuid4().hex[:10]` ⇒ unique per instance, e.g. `StutterTriage_PM_3f9a1c2b70`.

| Method | Returns | Semantics |
|---|---|---|
| `list_sessions()` | `list[str]` | Runs `logman query -ets` (15 s timeout), keeps lines containing `"Trace"` or `"Provider"`, first whitespace token = session name. Returns `[]` on any failure (never raises). |
| `stop_session(name)` | `bool` | Validation guard `re.match(r"^[A-Za-z0-9_.-]+$", name)` blocks command injection and returns `False` for invalid/empty names; otherwise runs `logman stop <name> -ets` (20 s timeout) and returns `True` (even if logman itself failed — `[UNCLEAR:1516]` it does not check the exit code). |
| `cleanup_stale(skip=None)` | `list[str]` | Stops every live session whose name starts with `PM_SESSION_PREFIX` (case-insensitive) except `skip`. Returns the names it attempted to stop. Called by `PresentMonBridge.run` with **no** `skip`, i.e. it also clears sessions of other StutterTriage instances. |
| `dispose()` | `None` | `stop_session(self.session_name)`; the mandatory safety net because a killed PresentMon leaves the ETW session `Running` (4 leftovers ⇒ error 1450 ⇒ silent 0-frame capture). |
| `alive()` | `bool` | `self.session_name in self.list_sessions()`. |

Threading: the class holds no locks; each call spawns a subprocess (up to 15–20 s). `PresentMonBridge.stop` deliberately runs `dispose()` on a throwaway daemon thread named `EtwCleanup` so the UI thread never pays the logman latency (lines 2264–2268).

---

## 6. `HardwareEngine` (line 1550–2076)

```python
class HardwareEngine(threading.Thread):
    NET_EXCLUDE = ("loopback","isatap","teredo","pseudo","pseudo-interface",
                   "tap-","tap ","vpn","vethernet","virtual","vmware",
                   "vbox","hyper-v","bluetooth","wsl","npcap","km-test")   # class attr, 1963-1965
    def __init__(self, interval, out_queue)
    def stop(self)
    def run(self)
    def assemble(self)
    def _pagein_top(self)
    def _emit(self, now, kind, text, advice="", severity="WARN",
              cooldown_key=None, cooldown=6.0, symbol="o")
    def _detect_anomalies(self, s)
    def _gpu(self, s)
    def _cpu(self, s)
    def _io_net(self, s)
    def _clock(self, s)
    def _mem(self, s)
```

### 6.1 Construction / lifecycle
- `__init__(self, interval, out_queue)`: `super().__init__(daemon=True, name="HardwareEngine")`; `self.interval = max(0.2, float(interval))` — **minimum polling interval 0.2 s**; the shipped default interval is 0.4 s (documented in the diagnostics card as the hardware sampling granularity).
- `out_queue` only needs `.put(item)`; the production instance is a `BoundedQueue(maxsize=60000)` whose `put` never blocks (drops the oldest item and counts it).
- Start: inherited `Thread.start()`.
- `stop()`: sets `self._stop_evt` and calls `self.gpu_probe.close()` (releases ADL/NVML; skipping it leaks the driver's ADL reference count). Non-blocking — the thread wakes from `_stop_evt.wait(dt)` and exits. Caller may `join()`; `stop()` itself does not join.
- **UI-safe calls: `stop()` only.** `assemble()` mutates engine state (`_seq`, `_pending`, `_prev_freq`, `_pf_*`, `_clk_*`) and must not be invoked concurrently with `run()`.

### 6.2 Polling loop (`run`, 1591–1635)
Opens one PDH session with 19 counter specs (keys: `a_ded, a_sha, p_ded, p_sha, p_loc, p_non, eng_util, cpu_time, cpu_perf, m_avail, m_commit, m_limit, m_pin, m_pout, p_io_rd, p_io_wr, p_io_oth, net_rx, n_tx`), then loops: `collect()` → `assemble()` → `out_q.put(("hw", snap))` → sleep `interval - elapsed` via `_stop_evt.wait(dt)`.

Degradation behaviour:
1. If `PdhSession.open(specs)` fails → emits one INFO `"系统"` event ("PDH 计数器打开失败，硬件探针以降级模式运行"); the loop continues with empty/zero counters.
2. Any exception in the loop body → `DIAG.report("HardwareEngine.run", ex)` + one INFO `"系统"` event; loop continues.
3. `assemble()` wraps all probe calls in try/except → `DIAG.report("HardwareEngine.assemble", ex)`; it still returns a (possibly partially filled) snapshot.
4. `_io_net` wraps the I/O half and the network half in separate try/except (`HardwareEngine._io_net(io)` / `(net)`); `_clock` wraps its whole body; `DIAG.report` is a no-op by default because `DIAG = DiagLog(enabled=False)` (line 640).
5. `GpuClockProbe` is lazily initialised; when absent, `gpu_clock`/`gpu_temp` stay `None` and `gpu_clock_max` stays 0.

### 6.3 Publication
- Snapshots: `self.out_q.put(("hw", snap))` where `snap` is a fully populated `HardwareSnapshot`.
- Events: `_emit(...)` → `self.out_q.put(("event", ev))`, with **cooldown suppression**: key = `cooldown_key or (kind, text)`; default cooldown 6.0 s; when the dict exceeds 300 entries it is pruned to the last 180 s. This is the only deduplication; there is no event backlog attribute.
- Nothing is published by attribute; the queue is the sole channel.

### 6.4 Event rules in `_detect_anomalies` (thresholds read from module globals)
| Rule | Condition | kind / severity / symbol | Cooldown key |
|---|---|---|---|
| CPU per-core downclock | `f0 > 500 MHz and (f0-f1)/f0 > FREQ_DROP_RATIO` and core usage > 5 % | `"降频"`; WARN if drop < 30 % else DANGER; `"v"` | `("drop", c)` / 8 s |
| ≥ 4 cores dropping at once | `len(drops) >= 4` | `"降频"`, INFO, `"t"`, text "判定为 Alt+Tab 切出窗口" | `("alttab",)` / 6 s |
| CPU0 jam | `cpu0_usage >= CPU0_JAM_THRESHOLD` **and** mean of other cores `< CPU0_IDLE_OTHERS` | `"CPU0拥堵"`, DANGER, `"s"` | `("cpu0_jam",)` / 8 s |
| Core migration | dominant core changed and `max_u > 60.0` and `abs(f_old-f_new) > MIGRATE_FREQ_GAP or core_variance > 450` | `"调度迁移"`, WARN, `"x"` | `("migration",)` / 10 s |
| VRAM spill | any `procs` row `nonlocal > SPILL_THRESHOLD` whose lower-cased name is **not** in `PROC_EVENT_WHITELIST` | `"溢出"`, DANGER, `"o"` | `("spill", pid)` / 15 s |
| Page-in peak | `page_in > 45.0` MB/s | `"内存"`, DANGER, `"p"` (suspects = `pagein_top` entries > 1.0 MB/s) | `("memhot",)` / 12 s |
| Shader burst | any `cache[tag]["new_files"] >= CACHE_BURST_FILES` | `"着色器"`, WARN, `"d"` | `("shader", tag)` / 8 s |
| Disk streaming | `io_read_mb >= IO_STREAM_WARN_MB`; DANGER when `>= IO_STREAM_DANGER_MB` | `"磁盘I/O"`, `"d"` | `("assetstream",)` / 12 s |
| GPU throttle | `gpu_clock` truthy **and** `_clk_ema > 500` **and** `max(gpu_eng.values()) > 30.0` **and** `(ema-clk)/ema > GPU_CLK_DROP_RATIO` | `"GPU降频"`, DANGER, `"v"` | `("gputhrottle",)` / 15 s |

`_clk_ema` is an EMA with α = 0.9 (new sample weight 0.1), converging in ≈ 4 s at 0.4 s polling.

### 6.5 Module-level threshold constants this class reads
`DIAG` (1630, 1663, 2017, 2040, 2066), `FREQ_DROP_RATIO` (1745), `CPU0_JAM_THRESHOLD` (1767), `CPU0_IDLE_OTHERS` (1770), `MIGRATE_FREQ_GAP` (1785), `SPILL_THRESHOLD` (1795), `PROC_EVENT_WHITELIST` (1795), `CACHE_BURST_FILES` (1813), `IO_STREAM_WARN_MB` (1825), `IO_STREAM_DANGER_MB` (1827), `GPU_CLK_DROP_RATIO` (1841). It also reads module functions/classes `human_size`, `pages_to_mb_per_s`, `NameTable`, `PdhSession`, `CacheWatcher`, `GpuClockProbe`, `dxgi_adapters`, `cpu_count`, `per_core_max_mhz` and the regexes `_RE_LUID` (1864), `_RE_ENGTYPE` (1890), `_RE_PID` (1897/1901/1905/1909). Full inventory in §11.

Hardcoded (non-constant) magic numbers inside this class: per-core freq drop needs `f0 > 500`, usage `> 5`; `len(drops) >= 4`; `avg_other`; dominant-core `max_u > 60.0`; `core_variance > 450`; `page_in > 45.0`; `pagein_top` entries `> 1.0`; `gpu_eng` `load3d > 30.0`; `_clk_ema > 500`; `_pf_last` gate `> 1.5 s`; working-set filter `20<<20`; process enumeration cap `2048` handles / first `1200` PIDs; `out[:5]` for page-in; `procs[:20]`; `io_keep = 20`; `io_top[:3]`; cooldown table `> 300` → prune `180 s`.

---

## 7. PresentMon layer (lines 2083–2573)

### 7.1 `_presentmon_search_roots()` (2083–2112)
`def _presentmon_search_roots()` → `list[str]`. Candidate roots, no hard-coded drive letters or version numbers: the script directory plus `PresentMon/`, `tools/`, `external/`, `bin/`; then, for every existing drive letter `A:\ … Z:\`, `<drive>\Program Files`, `<drive>\Program Files (x86)`, `<drive>\ProgramData`; then `~/Desktop`, `~/Downloads`, `~/Tools`, `~/Applications`, `~/.local`, `~/bin`. Non-Windows uses `["/"]`. Rebuilt on every call (no caching).

### 7.2 `_PM_CAP_CACHE = {}` (2116)
Module-level dict, key = `os.path.abspath(exe_path)`, value = `set[str]` of supported options. Unsynchronised; benign under the GIL for this use.

### 7.3 `detect_presentmon_capabilities(exe_path)` (2119–2153)
`def detect_presentmon_capabilities(exe_path)` → `set[str]` (empty set when `exe_path` is falsy). Runs `<exe> --help` (20 s timeout, `CREATE_NO_WINDOW` on Windows), lower-cases stdout+stderr, and records which of these literal options appear:
`"--v2_metrics"`, `"--v1_metrics"`, `"--set_circular_buffer_size"`, `"--no_track_input"`, `"--qpc_time_ms"`, `"--session_name"`, `"--stop_existing_session"`, `"--output_stdout"`, `"--no_console_stats"`, `"--timed"`, `"--terminate_after_timed"`, `"--track_frame_type"`, `"--track_app_timing"`, `"--write_display_metadata"`, `"--track_pc_latency"`.
Result is memoised in `_PM_CAP_CACHE`. Blocking (subprocess) — do not call per UI tick.

### 7.4 `find_presentmon_binary(explicit=None)` (2156–2202)
`def find_presentmon_binary(explicit=None)` → `str | None`. Search order:
1. `explicit` if it is an existing file, else `None` (note: an explicit non-file returns `None` immediately, it does **not** fall through);
2. environment variable `STUTTERTRIAGE_PRESENTMON` (if an existing file);
3. script dir + `PresentMon`/`tools`/`external`/`bin`, first `sorted(glob(root + "/*PresentMon*.exe"))`;
4. every root from `_presentmon_search_roots()` with depth 1–4 patterns `*PresentMon*.exe` … `*/*/*/*PresentMon*.exe`, accepting files **> 100 KiB**;
5. `shutil.which("PresentMon.exe") or shutil.which("PresentMon")`.
Blocking filesystem walk across all drives — call once at startup, not on the UI thread.

### 7.5 `PresentMonBridge` (2205–2573)

```python
class PresentMonBridge(threading.Thread):
    def __init__(self, out_queue, target_process=None, exe_path=None)
    def stop(self, wait=True, timeout=3.0)
    def run(self)
    def _status(self, ok, msg)
    def _run_simulation(self)
    def _parse_stream(self)
```

Public attributes after construction: `out_q`, `target_process` (`str|None`, **stored but never used inside the class** — `[UNCLEAR:2224]` filtering by process is done downstream via `FrameStore.visible_frames`/`dominant_app`), `exe_path` (`str|None`; the sentinel `"__FORCE_SIMULATION__"` forces simulation), `proc` (`subprocess.Popen|None`), `etw` (`EtwSessionManager`), `simulation_mode: bool` (False until the fallback runs), `frames_seen: int`, `last_error: str` (values seen: `""`, `"ETW 会话资源耗尽 (1450)"`, `"ETW 1450"`).
Private: `_stop_evt`, `_qpc_base`, `_wall_offset`, `_stderr_lines` (`deque(maxlen=200)`), `_stderr_thread`.

- `stop(wait=True, timeout=3.0)` → `None`. Sets the stop flag, `terminate()`s the child, optionally `wait(timeout)` then `kill()`; always starts a daemon thread `EtwCleanup` running `etw.dispose()` so the caller never pays logman latency; when `wait` and the bridge thread is alive and is not the current thread, joins with the same timeout.
  `[UNCLEAR:2234/2240]` the docstring claims the default is "do not wait (wait=False)", but the **actual signature default is `wait=True`**. Treat `wait=True` as the contract.
- `_status(ok, msg)` → publishes `("status", (bool, str))`. Consumed by the UI as a banner, not stored in `FrameStore`.
- `run()`:
  1. `exe_path == "__FORCE_SIMULATION__"` → status + `_run_simulation()`, return.
  2. `exe = exe_path or find_presentmon_binary()`; missing file → status + `_run_simulation()`, return.
  3. `etw.cleanup_stale()` (no `skip`) → optional positive status "已清理 N 个历史残留 ETW 会话".
  4. `caps = detect_presentmon_capabilities(exe)`; command line assembled **only from supported flags**: `--stop_existing_session`, `--session_name <etw.session_name>`, `--output_stdout`, `--no_track_input`, `--qpc_time_ms`, `--no_console_stats`, `--set_circular_buffer_size PM_BUFFER_SIZE`; if `caps` is empty it falls back to `["--output_stdout", "--session_name", <name>]`. `--v1_metrics`/`--v2_metrics` are deliberately **never** passed (default metric mode is the only mode containing `MsBetweenPresents`/`MsCPUBusy`/`MsGPUBusy`).
  5. Stop-flag re-check **before** `Popen` and again immediately after; the second check terminates the child and disposes the ETW session (race fix for orphan PresentMon processes).
  6. `Popen(..., stdout=PIPE, stderr=PIPE, text=True, bufsize=1, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)`; `Popen` failure → status + simulation.
  7. On success: status `"PresentMon 采集引擎就绪 [<basename>]"` then `_parse_stream()`.
- Frames are published as `("frame", FrameRecord)`; the simulation publishes the same tuple kind, so the consumer needs no special case.

### 7.6 Offline / simulated fallback (`_run_simulation`, 2280–2310)
Triggered by (a) the `"__FORCE_SIMULATION__"` sentinel, (b) a missing binary, (c) `Popen` failure, or (d) `frames_seen == 0` after `_parse_stream` ends without a stop request.
Deterministic stream: `np.random.default_rng(20261023)`, `base_ms = 8.33` (~120 FPS), `idx` cycling `% 420`; at cycle 150 VRAM-spill shape (ft = 6.5×base, gpu = 12 %, cpu = 85 % of ft), at 280 shader shape (4.2×, gpu 97 %), at 340 CPU0 shape (3.8×, gpu 20 %, cpu 92 %); otherwise `ft = max(2.0, base + N(0, 0.35))` with gpu 86–97 % and cpu 35–62 % of ft. Every frame is `FrameRecord(idx, now-t0, now, "GameSimulation.exe", 9999, ft, gpu, cpu)`; sleep is `ft/1000` seconds, i.e. real time pacing. `simulation_mode = True` is set at entry (declared externally in the UI as data source `DATA_SOURCE_SIM = "simulated"`).

### 7.7 CSV parsing contract (`_parse_stream`, 2402–2573)
- Silent-start tolerant: lines whose lower case starts with `"warning"`, `"error"`, `"started recording"`, `"stopped recording"` are skipped. The CSV header is the **first line containing a comma** (not necessarily line 1); columns are mapped **by name**, never by index (column count varies 18/19/21/26/32 with flags).
- `stderr` is drained by a dedicated daemon thread `PMStderrReader` (deadlock fix — the main loop never blocks reading stderr). Substring reactions in stderr: `"1450"` → `last_error = "ETW 会话资源耗尽 (1450)"` + negative status; `"events were lost"` → one-shot negative status "检测到 ETW 事件丢失…"; `"elevated privilege"` → positive status "PresentMon 未以管理员权限运行…".
- Per data row (parsed with `csv.reader`, requires ≥ 3 fields), the **exact column names** consulted, in order:
  | Logical value | Column names tried (first hit wins) | Notes |
  |---|---|---|
  | Application | `Application` | empty → `"<unknown>"` |
  | ProcessID | `ProcessID` | `int()` only if `isdigit()`, else `0` |
  | frametime (ms) | `MsBetweenPresents`, `msBetweenPresents`, `FrameTime` | NA/empty → row skipped; must be `math.isfinite` and `> 0.0` |
  | GPU busy (ms) | `MsGPUBusy`, `msGPUActive`, `GPUBusy` | via `safe_float(..., 0.0)` |
  | CPU busy (ms) | `MsCPUBusy`, `CPUBusy` | via `safe_float(..., 0.0)` |
  | capture time | `CPUStartQPCTimeInMs`, `CPUStartTimeInMs`, `CPUStartQPCTime` | first non-NA; treated as **absolute QPC in ms**; `t = (t_ms - first_t_ms)/1000` |
  `TimeInSeconds` is deliberately **never** used (its unit differs between default mode = ms and v1 mode = s).
- Wall-clock alignment: `off = now - t_ms/1000`; `_wall_offset` keeps the running **minimum** offset ("least-delayed frame"); `wall_ts = _wall_offset + t_ms/1000`. Without a usable time column: `t = now - t0`, `wall_ts = now`.
- Parse failures are counted through `DIAG.report("PresentMonBridge.parse_line", ex)` and skipped (dirty data is normal).
- Teardown always calls `etw.dispose()` and waits/kills the child (5 s). Then, if not stopped: 0 frames → negative status mentioning `last_error` (or "游戏未运行 / 无 DXGI Present / ETW 丢包") and `_run_simulation()`; > 0 frames → negative status "PresentMon 意外退出（已采到 N 帧）：<last stderr line>".

---

## 8. `BaselineTracker` (line 2580–2769)

```python
class BaselineTracker:
    def __init__(self, ema=None, window=None, spike_ratio=None, max_spike_frac=None)
    def reset(self)
    @staticmethod
    def _median(values, default=0.0)
    def update(self, frames, snap_lookup)
```

Defaults are the **module constants read at construction time**: `ema=BASELINE_EMA (0.25)`, `window=BASELINE_WINDOW (240)`, `spike_ratio=BASELINE_SPIKE_RATIO (2.2)`, `max_spike_frac=BASELINE_MAX_SPIKE_FRAC (0.35)`. A tracker therefore **freezes** those numbers at construction; after a config reload you must build a new tracker (`Config.baseline_kwargs()` exists for exactly that).

Public attributes (all of them are the values `SnapshotDiffInspector` consumes):

| Attribute | Type | Unit | Init | Meaning |
|---|---|---|---|---|
| `ema` | `float` | — | 0.25 | Old-value weight in the EMA (`new = a*old + (1-a)*sample`). |
| `window` | `int` | frames | 240 | How many trailing frames `update` samples. |
| `spike_ratio` | `float` | × median | 2.2 | A frame is "smooth" iff `frametime <= median * spike_ratio`. |
| `max_spike_frac` | `float` | fraction | 0.35 | Above this spike share the baseline is **frozen** (not updated). |
| `base_frametime` | `float` | ms | 16.67 | Baseline frame time (median of smooth frames, then EMA). |
| `base_fps` | `float` | fps | 60.0 | Recomputed `1000/base_frametime` at the end of every `update`. |
| `base_gpu_busy_ratio` | `float` | 0…1 | 0.0 | Median `gpu_busy_ratio` of smooth frames. |
| `base_cpu_busy_ratio` | `float` | 0…1 | 0.0 | Median `cpu_busy_ratio`. |
| `base_smooth_frames` | `int` | frames | 0 | `len(smooth)` of the last accepted window (diagnostic value). |
| `frozen` | `bool` | — | False | True when the last `update` refused to move the baseline. |
| `base_cpu0_usage` | `float` | % | 0.0 | Median `cpu0_usage` of the bound snapshots. |
| `base_cpu_total` | `float` | % | 0.0 | Median `cpu_total`. |
| `base_core_freq` | `list[float]` | MHz | `[]` | Per-core baseline. Built as median per index; EMA-updated only when the length is unchanged, else replaced wholesale. `[]` until the first accepted update (row 3 then reports no drop). |
| `base_vram_nonlocal` | `float` | bytes | 0.0 | Non-local VRAM baseline. |
| `base_vram_dedicated` | `float` | bytes | 0.0 | Dedicated VRAM baseline. |
| `base_vram_total` | `float` | bytes | 0.0 | Capacity baseline (0 ⇒ the card's percentage branch is skipped). |
| `base_page_in` | `float` | MB/s | 0.0 | Page-in baseline. |
| `base_page_out` | `float` | MB/s | 0.0 | Page-out baseline. |
| `base_cache_files` | `float` | files/round | 0.0 | Shader-cache new-file baseline. |
| `base_cache_mb` | `float` | MB/round | 0.0 | Shader-cache write baseline. |
| `base_gpu_clock` | `float` | MHz | 0.0 | **0 = unknown/unsupported.** Falsy samples are excluded from the median, and the EMA is skipped while the sample is 0, so an unsupported machine never drifts to a fake −100 %. |
| `base_io_read_mb` | `float` | MB/s | 0.0 | Disk-read baseline. |
| `base_net_rx_mb` | `float` | MB/s | 0.0 | NIC receive baseline. |
| `_primed` | `bool` | — | False | Private; first accepted update **assigns** instead of EMA-blending. |

Methods:
- `reset()` → `None`. Sets `_primed = False`, `frozen = False`, `base_core_freq = []`. **It does not clear the other baseline fields** `[UNCLEAR:2627]` — the next `update` overwrites them on the priming path only if it reaches the assignment branch (it does, because `_primed` is False).
- `_median(values, default=0.0)` → `float`; `np.median` of the values, or `default` when the sequence is empty.
- `update(frames, snap_lookup)` → `None`.
  - `frames`: iterable/sequence of `FrameRecord` in ascending time order (only `frames[-window:]` is used).
  - `snap_lookup`: `callable(seq) -> HardwareSnapshot | None` — `FrameStore.update_baseline` passes `self.snapshot_for`.
  - Returns immediately if `len(frames) < 20`, or if `med_ft <= 0`.
  - `spike_frac = 1 - len(smooth)/len(window)`; if `spike_frac > max_spike_frac and _primed` → `frozen = True` and **return without touching anything** (a stutter cluster must not drag the baseline). `frozen = False` otherwise.
  - If fewer than 10 smooth frames, the whole window is used.
  - Hardware side: dedupe `f.hw_seq` (only `>= 0`), resolve snapshots, take per-field medians; `gpu_clock`, `io_read_mb`, `net_rx_mb` are read via `getattr(s, ..., 0.0)` for forward compatibility.
  - Priming branch assigns every field; subsequent updates blend with `a = self.ema`. `base_core_freq` blends element-wise only when the lengths match.

---

## 9. `FrameStore` (line 2772–3269)

```python
class FrameStore:
    METRIC_WINDOW = 1000     # class attribute, line 3074
    def __init__(self, max_frames=100000, max_snaps=20000)
    def add_snapshot(self, snap)
    def add_event(self, ev)
    def add_frame(self, rec)
    @staticmethod
    def _app_key(rec)
    def _ensure_index(self)
    def _rebuild_index(self)
    def _nearest_frame(self, ts)
    def _bind_snapshot(self, wall_ts)
    def snapshot_for(self, seq)
    def visible_frames(self, target=None)
    def dominant_app(self)
    def summary_metrics(self, frames=None)
    def shader_activity(self, ts, window=1.0)
    def update_baseline(self, frames)
    def count_spikes(self, threshold)
    def session_correlations(self, frames=None)
```

### 9.1 Threading rule (verbatim intent)
Class docstring: “统一时序数据库（**仅由 UI 线程写入**，因此无需加锁）”. **There are no locks anywhere in the class.** `deque.append` is atomic in CPython, but the paired index updates (`_f_ts`/`_f_refs`/`_by_app`/`_ft_np`/`_app_cnt`) are not, and `_ensure_index` validates `_idx_n == len(frames)` plus identity of the last frame. Contract for the new UI: **exactly one thread (the Qt UI thread) may call `add_frame`/`add_snapshot`/`add_event`/`update_baseline`, and readers must be on that same thread.** Background producers must only touch the `queue`.

### 9.2 Public data the UI may read for charting
| Attribute | Container | Contents / order | Capacity |
|---|---|---|---|
| `frames` | `collections.deque` of `FrameRecord` | oldest → newest; the chart X axis is `rec.t` (s) or `rec.wall_ts`, the Y axis `rec.frametime` (ms) / `rec.fps` / `rec.gpu_busy` / `rec.cpu_busy` | `maxlen = max_frames` = **100000** (≈ 11.6 min @144 FPS, ≈ 27.8 min @60 FPS) |
| `snapshots` | `deque` of `HardwareSnapshot` | ascending `ts` | `maxlen = max_snaps` = **20000** (≈ 2.2 h @0.4 s) |
| `events` | `deque` of `Event` | append order | `maxlen = 600` |
| `cache_timeline` | `deque` of `(ts: float, new_files: int, new_mb: float)` | append order; the shader ±1 s window source | `maxlen = 2000` |
| `baseline` | `BaselineTracker` | see §8 | — |
| `METRIC_WINDOW` | class attribute `int` = 1000 | frames used by `summary_metrics` | — |

There is **no** `frames_window`, `latest`, `series`, `export_csv`, `clear`, or `capacity` method on `FrameStore` (verified by grep: the only remaining `def ...` in the class are the ones listed above). For charting, the UI copies `list(store.frames)` or `list(store.visible_frames(target))`; for CSV import/export and for clearing, the existing UI manipulates `store.frames` **directly** (`StutterTriageWindow._import_csv` at 6229 does `clear()`/`append()`), which is why `_ensure_index` performs an identity/`len` self-heal. The rewritten UI must keep that contract: after any direct mutation of `store.frames`, the next public call rebuilds the indices automatically (`_rebuild_index`), and that rebuild is O(n).

### 9.3 Writers
- `add_snapshot(snap)` → `None`. Appends to `snapshots`; updates `_snap_by_seq[snap.seq]`, `_snap_ts`, `_snap_seq`; trims the three index arrays to `len(snapshots)`; appends `(snap.ts, snap.cache_new_files, snap.cache_new_mb)` to `cache_timeline`; appends to the shader bisect timeline (`_ct_ts/_ct_files/_ct_mb`, marks `_ct_sorted = False` if a timestamp goes backwards) and compacts the timeline in ≥1024-element batches.
- `add_event(ev)` → `None`. Appends to `events` (always, even without frames); then, if frames exist, `_ensure_index()` + `_nearest_frame(ev.ts)`; if the nearest frame is within **`< 1.5` s** it sets `ev.frame = best` and appends `ev` to `best.events`. Events farther than 1.5 s from any frame are kept in `events` but attached to no frame.
- `add_frame(rec)` → `None`. **First** `rec.hw_seq = self._bind_snapshot(rec.wall_ts)`, then `_ensure_index()`, then appends to `frames` (evicting the oldest when `maxlen` is reached) and maintains, in O(1): `_f_ts`/`_f_refs` (with a 1024-step lazy front trim via `_f_start`), the `_by_app` shard (removing the evicted frame), the numpy ring `_ft_np`/`_ft_head`/`_ft_n`, `_idx_n`, and the 1200-frame rolling app counter `_app_win`/`_app_cnt`. `rec.app` is used verbatim (case-sensitive) for `_app_cnt`; the `_by_app` key is `rec.app.lower()`.

### 9.4 Readers
- `snapshot_for(seq)` → `HardwareSnapshot | None`. Exact dict lookup in `_snap_by_seq` (returns `None` for `-1`, evicted, or unknown seq). This is the **only** snapshot accessor.
- `visible_frames(target=None)` → `list[FrameRecord]`. Returns a **new list copy**. `target` in `(None, "all", "自动锁定主游戏")` → all frames; otherwise the pre-built shard for `target.lower()`, or `[]`. Calls `_ensure_index()` first, so it self-heals after external mutation.
- `dominant_app()` → `str | None`. Most frequent `app` within the last 1200 frames, from the incremental counter. Skips empty, the literal `"<unknown>"`, and any name whose lower case is in `NON_GAME_PROCESSES`; returns `None` when nothing qualifies. Note the comparison mixes case (`app.lower() in NON_GAME_PROCESSES`, whose members are all lower-case) while the returned value is the original case.
- `summary_metrics(frames=None)` → `dict`, keys and units exactly:
  `avg_fps` (fps), `low1_fps` (fps), `low01_fps` (fps), `smooth_pct` (%), `stutter_pct` (%), `severe_pct` (%), `gpu_busy_pct` (%), `total_frames` (int, = `len(frames)` passed in, **not** the window), `low01_degraded` (bool).
  `frames=None` ⇒ `list(self.frames)`. `n < 5` ⇒ all-zero dict with `smooth_pct = 100.0` and `low01_degraded = True`. Otherwise the last `METRIC_WINDOW` (1000) frames are used: `avg_fps = 1000/mean(frametime)`; `low1 = 1000/P99(frametime)`; `low01 = 1000/P99.9`, but when the sample is `< 1000` frames it degrades to `P99` and sets `low01_degraded = True`; the CapFrameX colour split is `smooth = ft < avg_ft*2.5`, `stutter = 2.5×…4.0×`, `severe = ft >= avg_ft*4.0`; `gpu_busy_pct = mean(gpu_busy_ratio)*100`.
  Result is cached in `_metric_cache` keyed by `(len(frames), frames[-1])` (identity) and reused while the frame set is unchanged.
- `shader_activity(ts, window=1.0)` → `tuple[int, float]` = `(new_files, new_mb)` inside `[ts-window, ts+window]`. `ts` is epoch seconds (callers pass `frame.wall_ts`). Uses `bisect` over the sorted timeline; if the timeline is unsorted/empty it falls back to a linear scan of `cache_timeline`. Returns `(0, 0.0)` for an empty interval.
- `count_spikes(threshold)` → `int`. Number of frames with `frametime >= threshold` (**threshold in ms**), computed with a vectorised numpy ring-buffer comparison. `_ensure_index()` first.
- `session_correlations(frames=None)` → `dict`. `frames=None` ⇒ `list(self.frames)`. Returns `{}` when `len(frames) < 30`, when `baseline.base_frametime <= 0`, or when there is no spike. Spikes are `frametime >= base_frametime * 1.8`. Result shape: `{name: {"hits": int, "total": int, "rate": float, "confidence": str}}` with the five **exact** names and predicates:
  | `name` | predicate (all use the binding snapshot via `snapshot_for`) |
  |---|---|
  | `"着色器编译爆发"` | `shader_activity(f.wall_ts, 1.0)[0] >= CACHE_BURST_FILES` |
  | `"CPU0 拥堵"` | `hw.cpu0_usage >= CPU0_JAM_THRESHOLD` and mean of `hw.cpu_usage[1:]` `< CPU0_IDLE_OTHERS` |
  | `"显存溢出"` | `hw.vram_nonlocal > SPILL_THRESHOLD` |
  | `"内存换入压力"` | `hw.page_in > baseline.base_page_in + 40.0` (hardcoded 40 MB/s) |
  | `"GPU 饥饿"` | `f.gpu_busy > 0` and `(base_gpu_busy_ratio - f.gpu_busy_ratio)*100 >= 35.0` |
  `rate = hits/total`; `confidence` = `"高"` (≥ 0.6), `"中"` (≥ 0.35), `"低"` (≥ 0.15), else `"极低（可能是巧合）"`. Entries with `hits <= 0` are omitted. Cached in `_corr_cache` keyed by `(len(frames), frames[-1], base_ft)`.
- `update_baseline(frames)` → `None`. `self.baseline.update(frames, self.snapshot_for)` then invalidates `_corr_cache`.

Private helpers a rewriter may need to know exist (do not rely on them): `_app_key` (static: `(rec.app or "").lower()`), `_ensure_index`, `_rebuild_index`, `_nearest_frame(ts)` → `(FrameRecord|None, |Δt||None)` (bisect over `_f_ts`, then a ±2 window scan, tolerating slight disorder), `_bind_snapshot(wall_ts)` → `int` (bisect_right on `_snap_ts` minus 1, clamped into `[0, len(_snap_seq)-1]`; returns `-1` when no snapshots exist) — i.e. "the nearest snapshot **not later than** the frame".

---

## 10. `SnapshotDiffInspector` (line 3272–3728)

```python
class SnapshotDiffInspector:
    @staticmethod
    def inspect(frame: FrameRecord, store: FrameStore)     # -> list[DiffRow]
    @staticmethod
    def classify_gc_lock(frametime, gpu_ratio, cpu_ratio, base_frametime)   # -> dict
    @staticmethod
    def banner_verdict(rows, frame)                        # -> str
```

### 10.1 `inspect(frame, store)` — always exactly **11 rows, in this order**
Header semantics of the four columns: `监控维度 | 异常瞬间数值 (T) | 平稳期均值 (Baseline) | 变化幅度 (Delta) | 元凶判定` (the last is `DiffRow.verdict`).

Staleness gate used by rows 3, 4, 5, 7, 8, 9:
`hw = store.snapshot_for(frame.hw_seq)`; `hw_age = frame.wall_ts - hw.ts` (or `None`); `hw_stale = (hw is None) or (hw_age > MAX_HW_AGE)` with `MAX_HW_AGE = 1.2` s. Stale/absent rows are appended as undecidable rows with `cur` = `"无该时刻硬件快照"` or `"硬件快照已过期 (%.1fs)"`, `base = delta = "—"`, `verdict = "⚪ …，本行不可判定"`, `severity = "NORMAL"`.

| # | `dim` (exact prefix; `banner_verdict` matches by `startswith`) | `cur` / `base` / `delta` format | Thresholds | Severity |
|---|---|---|---|---|
| 1 | `帧表现 (帧耗时 / 瞬时 FPS)` | `"%.1f ms (%.0f FPS)"`; delta via `fmt_delta(cur_ft, base_ft, "ms")` | `cur_ft >= base_ft*3.0` → DANGER; `>= base_ft*1.8` → WARN; else NORMAL | DANGER/WARN/NORMAL |
| 2 | `GPU 忙时比 (显卡饥饿判定)` | `"%.1f%% (%.2f ms)"` / `"%.1f%%"` / `"%+.1f pt"`; uses percent of `gpu_busy_ratio` | `starved = (base-cur) >= 35.0 pt and cur_ft >= base_ft*1.8`; `overloaded = (cur-base) >= 12.0 pt and cur_ft >= base_ft*1.8`; `frame.gpu_busy <= 0` ⇒ "未拿到 GPU 忙时数据" NORMAL | DANGER/WARN/NORMAL |
| 3 | `CPU 核心状态 (降频 / CPU0 拥堵)` | either `"CPU0 %.0f%% · CPU%d %.0fMHz"` or `"CPU0 %.0f%% · 均频 %.0fMHz"`; delta `fmt_delta(f, base_f, "MHz")` or `"%+.0f pt"` | `hw.cpu0_usage >= CPU0_JAM_THRESHOLD (92.0)` → DANGER; else worst core with `base > 200 MHz`, `usage > 5.0`, drop `>= 20.0 %` → WARN; else NORMAL | DANGER/WARN/NORMAL |
| 4 | `显存溢出 (溢出到系统内存)` | `"非本地 %.0f MB / 专用 %.0f%%"` (+ `"（<proc> 溢出 <size>）"` when the worst non-whitelisted process exceeds `SPILL_THRESHOLD`); delta `"%+.0f MB"` | `cur_spill (>64.0 MB) and spill_delta (>15.0 MB)` → DANGER; elif `vram_pct > 94.0` → WARN; `hw.vram_total == 0` ⇒ `vram_pct = 0.0` | DANGER/WARN/NORMAL |
| 5 | `页面文件换入 & 缺页嫌疑进程` | `"%.1f MB/s · Top: <names>"`; delta `"%+.1f MB/s"` | `pin_delta >= 40.0 and cur_pin >= base_pin+40.0` → DANGER; `pin_delta >= 12.0` → WARN | DANGER/WARN/NORMAL |
| 6 | `着色器编译活动 (±1 秒窗口)` | `"+%d 个文件 (+%.1f MB)"` / `"每轮约 %.1f 个"` / `"%+d"`; **does not need a snapshot** | `files >= CACHE_BURST_FILES (8) or mb >= 2.0` → DANGER; `files >= 2` → WARN | DANGER/WARN/NORMAL |
| 7 | `磁盘 I/O 资产流式加载` | `"%.1f MB/s"` (+`"（近似值）"`); delta `fmt_delta(io_cur, base_io, "MB/s")` | `io_cur >= IO_STREAM_DANGER_MB (50)` → DANGER; `>= IO_STREAM_WARN_MB (20)` → WARN | DANGER/WARN/NORMAL |
| 8 | `网络同步吞吐 (多人 Tick)` | `"↓%.0f KB/s ↑%.0f KB/s"` (+ `" · 游戏进程 %.0f KB/s"`); base `"↓%.0f KB/s"`; delta `"%+.0f KB/s"` (all KB/s = MB/s × 1024) | `stall = base_rx_kb >= NET_STALL_RX_KB (20) and rx_kb <= base_rx_kb*NET_STALL_RATIO (0.25) and cur_ft >= base_ft*1.8` → DANGER; elif `base_rx_kb >= 20 and rx_kb <= base_rx_kb*0.5` → WARN (the 0.5 is hardcoded, not `NET_STALL_RATIO`) | DANGER/WARN/NORMAL |
| 9 | `GPU 核心频率 / 撞墙降频` | `"%.0f MHz · %.0f℃"`; base `"%.0f MHz"` or `"—"`; delta `fmt_delta(clk, base_clk, "MHz")` or `"—"` | `base_clk > 100 and drop_pct > GPU_CLK_DROP_RATIO*100 (15 %)` → DANGER; `base_clk > 100 and drop_pct > 8.0` → WARN; falsy `gpu_clock` ⇒ `"不可用"` and NORMAL ("本机未提供 GPU 核心频率") | DANGER/WARN/NORMAL |
| 10 | `引擎 GC / 主线程锁死` | `cur` and `text` come from `classify_gc_lock`; `base` is the literal `"尖峰时 GPU 与 CPU 均不应同时空闲"`; `delta = "—"` | delegate to `classify_gc_lock` (§10.2) | from `classify_gc_lock` |
| 11 | `会话级归因置信度` | `"本次会话共 %d 次卡顿"` (from `store.count_spikes(base_ft*1.8)`) / `"按诱因统计重合率"` / `"—"`; verdict `"📊 会话级证据：<name> hits/total (rate%·置信度conf)；…"` sorted by descending `rate`, or `"📊 会话级证据：样本不足，暂无统计结论"` | none | always NORMAL |

Row 7 process attribution (`io_by_name` lookup) tries, in this order: `frame.app.lower()`, the same minus a trailing `.exe`, then the first 15 characters; falling back to `hw.io_read_mb` + `hw.io_top[0][0]` and marking the value `（近似值）`. Row 8 reuses the **same** `io_by_name` tuple's element `[1]` (which is `IO Other Bytes/sec`, not a network counter) as the per-game value and labels it KB/s — this is intentional in the source but semantically an approximation, `[UNCLEAR:3542]`.

### 10.2 `classify_gc_lock(frametime, gpu_ratio, cpu_ratio, base_frametime)` → `dict`
Return keys, always all six: `{"hit": bool, "severity": str, "cur": str, "gpu_pct": float, "cpu_pct": float, "text": str}`.
- Inputs: `frametime` ms, `gpu_ratio`/`cpu_ratio` **fractions 0…1** (multiplied by 100 inside), `base_frametime` ms.
- `cur = "%.1f ms · GPU %.0f%% / CPU %.0f%%"`.
- `thr = max(GC_SPIKE_ABS_MS (50.0), base_ft*GC_SPIKE_RATIO (3.0))` when `base_ft > 0`, else `50.0`. Below it: `hit=False`, `NORMAL`, "🟢 无严重尖峰…，GC 不成立".
- Missing-GPU guard: `gpu_ratio <= 1e-4` ⇒ `hit=False`, `NORMAL`, "⚪ 本帧缺少 GPU 忙时数据（需默认 metric 模式），GC 不可判定" (NA is parsed as 0.0 and must not be read as "GPU idle").
- `gpu_pct < GC_LOW_UTIL (20.0)` **and** `cpu_pct < 20.0` ⇒ `hit=True`, `DANGER`, "🔴 引擎底层 GC / 主线程锁死…".
- Else `gpu_pct >= 80.0` ⇒ WARN (GPU-bound, not GC); `cpu_pct >= 80.0` ⇒ WARN (CPU-bound, not GC); otherwise `NORMAL` "🟢 尖峰期间 GPU/CPU 均有负载…".
- Pure, side-effect free, no `store` access — safe on any thread and suitable for unit tests.

### 10.3 `banner_verdict(rows, frame)` → `str`
Returns the verdict string of the highest-priority **cause** row. `frame` is accepted but **never referenced** in the body `[UNCLEAR:3702]` — a rewriter may drop the parameter, though keeping it preserves the call signature.
Priority key tuple, exact order (matched with `r.dim.startswith(key)` and `r.severity in ("DANGER", "WARN")`):
`("GPU 核心频率", "磁盘 I/O", "网络同步", "GPU 忙时比", "显存溢出", "页面文件换入", "CPU 核心状态", "着色器编译", "引擎 GC")`.
The "帧表现" row is deliberately excluded (it is a symptom, not a cause). Fallbacks in order: first `DANGER` row, then first `WARN` row, then the literal `"🟢 各项硬件指标均在平稳基线附近，无掉帧诱因"`.

---

## 11. Module-level constants referenced by these ranges (name, value, definition line)

Threshold / behaviour constants (all in the `§1b 模块级常量` block unless noted):

| Constant | Value | Def line | Used in range at |
|---|---|---|---|
| `SPILL_THRESHOLD` | `64 << 20` = 67108864 bytes (64 MiB) | 512 | 1795, 3225, 3418 |
| `FREQ_DROP_RATIO` | `0.18` | 513 | 1745 |
| `CACHE_BURST_FILES` | `8` (int) | 514 | 1813, 3209, 3471 |
| `CPU0_JAM_THRESHOLD` | `92.0` (%) | 515 | 1767, 3215, 3362 |
| `CPU0_IDLE_OTHERS` | `70.0` (%) | 516 | 1770, 3219 |
| `MIGRATE_FREQ_GAP` | `400.0` (MHz) | 517 | 1785 |
| `MAX_HW_AGE` | `1.2` (s) | 518 | 3291 |
| `IO_STREAM_WARN_MB` | `20.0` (MB/s) | 521 | 1825, 3513 |
| `IO_STREAM_DANGER_MB` | `50.0` (MB/s) | 522 | 1827, 3509 |
| `GPU_CLK_DROP_RATIO` | `0.15` (fraction) | 523 | 1841, 3587 |
| `GC_LOW_UTIL` | `20.0` (%) | 524 | 3681 |
| `GC_SPIKE_ABS_MS` | `50.0` (ms) | 525 | 3671 |
| `GC_SPIKE_RATIO` | `3.0` (×) | 526 | 3671 |
| `NET_STALL_RX_KB` | `20.0` (KB/s) | 527 | 3544, 3552 |
| `NET_STALL_RATIO` | `0.25` | 528 | 3545 |
| `PROC_EVENT_WHITELIST` | `{"amdrsserv.exe", "amdrsservice.exe"}` | 529 | 1795, 3402 |
| `NON_GAME_PROCESSES` | 20-name `set` (dwm/explorer/chrome/msedge/firefox/msedgewebview2/wallpaper32/steamwebhelper/discord/searchhost/textinputhost/shellexperiencehost/startmenuexperiencehost/amdow/nvcontainer/rtss/obs64/wechat/tim/vlc/potplayer) | 533–539 | 3067 |
| `PM_SESSION_PREFIX` | `"StutterTriage_PM_"` (sentinel string) | 542 | 1487, 1533 |
| `PM_BUFFER_SIZE` | `4096` (must be a power of two) | 543 | 2353 |
| `BASELINE_WINDOW` | `240` (frames) | 546 | 2599 |
| `BASELINE_EMA` | `0.25` | 547 | 2598 |
| `BASELINE_SPIKE_RATIO` | `2.2` | 548 | 2600 |
| `BASELINE_MAX_SPIKE_FRAC` | `0.35` | 549 | 2601 |
| `_RE_PID` | `re.compile(r"pid_(\d+)")` | 551 | 1897, 1901, 1905, 1909 |
| `_RE_LUID` | `re.compile(r"luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)")` | 552 | 1864 |
| `_RE_ENGTYPE` | `re.compile(r"engtype_([A-Za-z0-9_]+)")` | 553 | 1890 |
| `DIAG` | `DiagLog(enabled=False)` — `report`/`note` are no-ops by default | 640 | 1630, 1663, 2017, 2040, 2066, 2546 |
| `_PM_CAP_CACHE` | `{}` — **defined inside the range** | 2116 | 2134, 2135, 2152 |

Constants read indirectly (through helper classes referenced by the ranges, defined outside them): `CACHE_DIRS` (1245, 7 tag/`lambda` pairs), `CacheWatcher.SCAN_BUDGET_MS = 250.0` (1270), `CacheWatcher` scan cadence bounds `1.5 … 5.0` s, `PdhSession` + `PDH_FMT_DOUBLE = 0x00000200` (753), `PDH_MORE_DATA = 0x800007D2` (754), `PG_SYMBOLS` (558, the legal `Event.symbol` set), `GpuClockProbe` + `ADL_PMLOG_SENSOR_GFXCLK = 1` (934), `ADL_PMLOG_SENSOR_EDGE_TEMP = 8` (935), `ADL_SENSOR_INVALID = 65535` (936), `BoundedQueue(maxsize=60000)` (696), `human_size` (643), `pages_to_mb_per_s` (656, ×4096/1048576), `safe_float` (664), `is_na` (726, NA/`N/A`/empty ⇒ True), `fmt_delta` (733, returns `"—"`/`"持平"`/`"▲+x.x%"`/`"▼+x.x<unit>"`).

Config-driven write-back targets (outside the range, but they are the same globals — see §12): `CONFIG_SCHEMA` (7345–7370) maps config keys to `SPILL_THRESHOLD`, `FREQ_DROP_RATIO`, `CACHE_BURST_FILES`, `CPU0_JAM_THRESHOLD`, `CPU0_IDLE_OTHERS`, `MIGRATE_FREQ_GAP`, `PM_BUFFER_SIZE`, `BASELINE_WINDOW`, `BASELINE_EMA`, `BASELINE_SPIKE_RATIO`, `BASELINE_MAX_SPIKE_FRAC`, `MAX_HW_AGE`, and `Config.apply_to_globals` (7523) writes them into the defining module's globals at runtime (with `PM_BUFFER_SIZE` forced to a power of two and the two CPU0 percentages clamped to 0…100).

---

## 12. Re-homing inventory: module-level globals read/written inside lines 1357–3730

**Writes (the only one):**
- `_PM_CAP_CACHE` — mutated at 2135 (`key in _PM_CAP_CACHE` read) and 2152 (`_PM_CAP_CACHE[key] = caps`); it is *defined* at 2116, inside the range. Dict mutation without a lock.

**Reads (name → exact line numbers inside the range):**
- `DIAG` — 1630, 1663, 2017, 2040, 2066, 2546
- `FREQ_DROP_RATIO` — 1745
- `CPU0_JAM_THRESHOLD` — 1767, 3215, 3362
- `CPU0_IDLE_OTHERS` — 1770, 3219
- `MIGRATE_FREQ_GAP` — 1785
- `SPILL_THRESHOLD` — 1795, 3225, 3418
- `PROC_EVENT_WHITELIST` — 1795, 3402
- `CACHE_BURST_FILES` — 1813, 3209, 3471
- `IO_STREAM_WARN_MB` — 1825, 3513
- `IO_STREAM_DANGER_MB` — 1827, 3509
- `GPU_CLK_DROP_RATIO` — 1841, 3587
- `_RE_LUID` — 1864; `_RE_ENGTYPE` — 1890; `_RE_PID` — 1897, 1901, 1905, 1909
- `PM_SESSION_PREFIX` — 1487, 1533; `PM_BUFFER_SIZE` — 2353
- `NON_GAME_PROCESSES` — 3067
- `MAX_HW_AGE` — 3291
- `GC_SPIKE_ABS_MS` — 3671; `GC_SPIKE_RATIO` — 3671; `GC_LOW_UTIL` — 3681
- `NET_STALL_RX_KB` — 3544, 3552; `NET_STALL_RATIO` — 3545
- `BASELINE_EMA` — 2598; `BASELINE_WINDOW` — 2599; `BASELINE_SPIKE_RATIO` — 2600; `BASELINE_MAX_SPIKE_FRAC` — 2601
- Environment variable `STUTTERTRIAGE_PRESENTMON` — 2167 (a process-global read; keep it configurable)

**Module-level functions/classes the ranges import from the flat module** (must be re-homed or re-imported): `human_size` (1797, 3419), `pages_to_mb_per_s` (2075, 2076), `is_na` (2495, 2523), `safe_float` (2511, 2512, 2524), `fmt_delta` (3309, 3374, 3522, 3586), `NameTable`, `PdhSession`, `CacheWatcher`, `GpuClockProbe`, `dxgi_adapters`, `cpu_count`, `per_core_max_mhz`, plus the stdlib/third-party names `time, os, re, csv, math, glob, shutil, subprocess, uuid, threading, ctypes, np (numpy), bisect, collections.deque, queue`.

**Globals deliberately NOT referenced in the range** (verified: the nearest matches for each are at lines ≤ 509 or ≥ 3757): `BG`, `PANEL`, `PANEL2`, `BORDER`, `BORDER_HOT`, `FG`, `DIM`, `ACCENT`, `ACCENT_D`, `WARN`-the-colour, `BAD`, `PURPLE`, `ORANGE`, `GREEN`, `CYAN_DIM`, `FONT_FAMILY`, `MONO_FAMILY`, `PG_SYMBOLS`, `CURRENT_THEME`, `THEME_*`, `apply_theme_to_globals`, `CURRENT_CHART_STYLE`, `CX_*`, `DATA_SOURCE_*`, `CONFIG_*`, `HEAT_STOPS`, `SIM_WATERMARK_TEXT`, `PRESENTMON_GUIDE`, `WM_HOTKEY`, `MOD_*`.
⇒ **The core (models, hardware probes, PresentMon bridge, store, diagnostics) has zero presentation-layer coupling.** Splitting the file needs no colour re-homing inside the core; only the threshold/log/helper names above.
Caveat for the rewrite: several appearance constants are *dynamically rewritten* at runtime (`apply_theme_to_globals`, line 349, writes `BG/PANEL/PANEL2/BORDER/BORDER_HOT/FG/DIM/ACCENT/ACCENT_D/WARN/BAD/PURPLE/ORANGE/GREEN` into the module that owns them; `CURRENT_THEME` is a `global` in `_apply_theme`, line 4439; `CURRENT_CHART_STYLE` is a `global` at 4579 and 6500). The threshold constants are likewise rewritten by `Config.apply_to_globals`. **Therefore the core modules must read these names through module-attribute access at call time (e.g. `core.SPILL_THRESHOLD`), never via `from core import SPILL_THRESHOLD`,** otherwise a config/theme reload will silently stop affecting the core. The only place a value is snapshotted is `BaselineTracker.__init__`, which by design captures the four `BASELINE_*` values (rebuild the tracker after a config change).

---

## 13. Sentinel strings, capacities and hardcoded magic numbers inside the ranges

Sentinel strings: `"__FORCE_SIMULATION__"` (exe_path, 2315); `"<unknown>"` (app fallback 2486, excluded in `dominant_app` 3065); `_Total` (PDH instance, 1957); instance names starting `"_"` skipped (1984); `"idle"` skipped (1987); line-skip prefixes `"warning"/"error"/"started recording"/"stopped recording"` (2461); stderr markers `"1450"`, `"events were lost"`, `"elevated privilege"` (2433/2438/2443); `last_error` values `"ETW 会话资源耗尽 (1450)"` (2434) and `"ETW 1450"` (2463); `visible_frames` pass-through targets `None`, `"all"`, `"自动锁定主游戏"` (3040); `"—"` / `"持平"` (from `fmt_delta`); verdict emoji prefixes `🔴 🟠 🟢 ⚪ 📊`; the default banner `"🟢 各项硬件指标均在平稳基线附近，无掉帧诱因"` (3728).

Capacities / windows: `FrameStore(max_frames=100000, max_snaps=20000)`; `events` 600; `cache_timeline` 2000; shader timeline compaction at 2000/1024; `METRIC_WINDOW = 1000`; app rolling window 1200; `_io_keep = 20`; `procs[:20]`; `pagein_top[:5]`; `io_top[:3]`; `_evt_cooldown` prune `> 300` entries / 180 s; `_stderr_lines = deque(maxlen=200)`; `K32EnumProcesses` 2048 handles with `min(count, 1200)` iterations; working-set filter `20<<20`; PDH ring `PM_BUFFER_SIZE = 4096`; `BoundedQueue` default 60000.

---

## 14. `[UNCLEAR]` / discrepancy list

1. `[UNCLEAR:2234]` `PresentMonBridge.stop` docstring says the default is "do not wait (`wait=False`)", but the signature default is `wait=True`.
2. `[UNCLEAR:2224]` `PresentMonBridge.target_process` is stored and documented but never used anywhere in the class; process targeting happens later via `FrameStore.visible_frames`/`dominant_app`.
3. `[UNCLEAR:1516]` `EtwSessionManager.stop_session` returns `True` without inspecting `logman`'s exit code.
4. `[UNCLEAR:2627]` `BaselineTracker.reset()` clears only `_primed`, `frozen`, `base_core_freq`; the other `base_*` fields keep their values until the next accepted `update`.
5. `[UNCLEAR:3542]` the "网络同步吞吐" row reads the game process value from `io_by_name[name][1]`, which is `IO Other Bytes/sec` (a disk/file I/O counter), not a network counter.
6. `[UNCLEAR:3702]` `banner_verdict(rows, frame)` never uses `frame`.
7. `[UNCLEAR:3296]` row 1 computes `ft_delta` but does not use it (the delta text comes from `fmt_delta`).
8. `[UNCLEAR:3385]` the stale branches interpolate `hw_age` with `%.1f` while `hw_age` can be `None`; this branch is only reachable when `hw is not None`, so it is safe in practice.
9. `[UNCLEAR:1936]` per-core PDH instance names are tried as `"0,%d"` then `"%d"`; on machines whose PDH instance naming differs, `cpu_usage`/`cpu_freq` degrade to zeros rather than erroring.
10. `[UNCLEAR:2068]` `_mem` takes `next(iter(values))` of each instance dict — correct only because these are single-instance counters.
11. `[UNCLEAR:3074]` `METRIC_WINDOW` is a class attribute, so changing it on an instance is possible but `summary_metrics` reads `self.METRIC_WINDOW` (instance override would work).
12. Not a defect, but load-bearing for the rewrite: `FrameStore` has no capacity reporting, no `latest`, no `series`, no `clear`, and no CSV export. Charting must read `store.frames` / `store.visible_frames(target)`; CSV import/export lives in the UI class (`_import_csv` 6229, `_export_csv` 6417) and mutates `store.frames` directly.

---

## 15. 判定口径与纯函数：判定口径与新增纯函数（`stuttertriage/stats.py` / `stuttertriage/consts.py`）

本节是拆分 & 二次重构之后的**增量契约**；第 0~14 节描述的单体 API 依然有效。

### 15.1 新增阈值（`stuttertriage/consts.py`，均已登记进 `LIVE_CONST_NAMES` 可热更新）

| 常量 | 默认 | 含义 |
|---|---|---|
| `VRAM_SPILL_MIN_RATIO` | `0.88` | 物理专用显存占用率门槛：低于此值**永不**判「爆显存」 |
| `VRAM_SPILL_MIN_MB` | `256.0` | 非本地（共享）显存绝对值门槛（MB） |
| `VRAM_SPILL_DELTA_MB` | `64.0` | 非本地显存较平稳基线的最小暴增量（MB） |
| `GPU_DOWNCLOCK_MIN_BASE_MHZ` | `1200.0` | 基线核心频率门槛；`< 1000` 直接视为省电待机档 |
| `GPU_DOWNCLOCK_MIN_3D_LOAD` | `60.0` | 3D 引擎负载门槛（%）；低于此为正常节能 |
| `GPU_CLK_DROP_RATIO` | `0.20` | 撞墙降频所需的最小跌幅（原 `0.15`） |

`SnapshotDiffInspector.inspect` 第 4 行「显存溢出」的 DANGER 需四个条件同时成立：
`vram_total > 0` ∧ `dedicated/total ≥ 0.88` ∧ `nonlocal_mb ≥ 256` ∧ `spill_delta ≥ 64`。
第 9 行「GPU 核心频率 / 撞墙降频」的 DANGER 需：`base_clk ≥ 1200` ∧ `3D 负载 ≥ 60%`
∧ `drop > 20%` ∧ 本帧 `cur_ft ≥ base_ft × 1.8`。

### 15.2 `stuttertriage/stats.py` 新增 / 变更的纯函数

| 函数 | 契约 |
|---|---|
| `local_median_fps(frames, fts=None, window=STUTTER_WINDOW) -> np.ndarray` | 与 `frames` 等长的**局部中位数帧率**数组；第 i 帧 = `median(fps[max(0,i-w+1):i+1])`。内部有增量缓存（窗口滑动时只补算尾帧），长会话内存有上限 |
| `stutter_flags(frames, fts=None, window=STUTTER_WINDOW) -> np.ndarray[bool]` | 严格顿挫：**A** `cur_fps ≤ 局部中位数×0.65` ∧ **B** `cur_ft − 局部中位数耗时 ≥ 8.0ms` ∧ **非 C** `cur_fps ≥ 85` 豁免；第 0 帧恒 `False` |
| `is_stutter_frame(frames, index, fts=None, window=...) -> bool` | 单帧判定（越界/第 0 帧 → `False`） |
| `drop_flags(frames, fts=None)` | **签名不变**，语义已切换为 `stutter_flags`；`DROP_FT_FACTOR` / `DROP_FPS_FACTOR` 仅保留定义不再参与判定 |
| `window_stats(frames, window=STAT_WINDOW)` | 保留全部旧键；**新增** `flags`（bool 数组）/ `stutter_now` / `local_median_fps` / `stutter_idx` / `smooth`。`dropped` / `dropped_pct` 改为严格顿挫口径 |
| `gpu_3d_load_pct(hw) -> float` | 3D 引擎负载（%）：键名含 `3d` 优先，`≤1.0` 视为比例自动 ×100，缺失则退回所有引擎最大值；`hw is None → 0.0` |
| `vram_bar_text(dedicated_mb, total_mb, nonlocal_mb=None) -> (text, pct)` | `text` = `已用 X.XX GB / 总计 X.XX GB (NN%)`；`pct` 严格 `clamp(0,100)`；`total ≤ 0` → `("容量未知", 0.0)` |

新增模块常量：`STUTTER_WINDOW = 120`、`STUTTER_REL_FACTOR = 0.65`、`STUTTER_FT_JUMP_MS = 8.0`、`STUTTER_EXEMPT_FPS = 85.0`。

### 15.3 UI 层新增模块（不再属于「引擎契约」，仅登记）

`ui/navtabs.py`（5 张导航卡）、`ui/pages.py`（`FpsOverviewPage` / `CpuPage` / `GpuPage` / `MemPage` / `NetPage`，统一 `refresh(hw, ctx)` 与 `badge(hw, ctx)` 接口）、
`ui/gauge.py` 新增 `CpuFreqBars` / `VramSplitBar` / `vram_text()`；`ui/accordion.py` 已删除；
`ui/diagnosis.MiniDiagnosisCard` 新增 `show_smooth()`（绿色「全程运行丝滑」态）与 `is_smooth_state()`。
`chart.FpsChart` 新增 `ms_ref_lines`（16.7 / 33.3 ms 两条参考虚线）并锁死坐标轴（见 `README.md` §3.2 表）。
