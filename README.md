# FOV Unlock —— 绝地潜兵2 垂直视野解锁（突破 90 上限）

把 **Options → Visuals → Vertical Field of View（垂直视野）** 的硬上限 90 去掉，
可用 **45–175** 的任意值。游戏滑条本身只给 45–90 —— 本 mod 改的是引擎侧的夹取。

需要 **Bingus Shared Loader v15+**（唯一依赖，没有第二个 mod）。

---

## ⚠️ 先读：默认是纯数据模式

**改 `game.dll` 的可执行页（`.text`）会触发 nProtect GameGuard，游戏直接关闭。**
这是实测结论（装了会改 `.text` 的 v1.x 之后出现，已确认是代码段补丁导致）。

| 通道 | 默认 | 动作 | GG 风险 |
|---|---|---|---|
| 活设置对象（数据） | **开** | 往堆上的设置结构写 4 字节 | 无（同其它 Bingus 数据类 mod） |
| 写 `user_settings.config` | **开** | 改一个文本文件 | 无 |
| 代码段上限补丁 | **关** | `VirtualProtect` 成 RWX + 改 `minss` | **会触发 GG 关游戏** |

代码段那条要 `patch_code_clamp = true` 才开，**风险自负**。数据路径结构上写不到代码页：
它不声明 `VirtualQuery`、不调 `VirtualProtect`，直接 `WriteProcessMemory` ——
可执行页是只读的，**由内核拒绝**。

---

## 安装

1. 导入本 ZIP + Bingus Shared Loader，启用，Deploy。
2. 看 `%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_UNLOCK_STATUS.txt`，
   **第一行就是结论**（`OK - ...` / `FAILED - ...`）。

> 别手动把 `Addon\` 拷进 `data\`：管理器部署时会重排 `patch_N`，手动拷会和别的 mod 互相覆盖。

## 配置

`%APPDATA%\Arrowhead\Helldivers2\fov_unlock.cfg`（没有就用默认 `fov = 100`）：

```ini
enabled = true
fov = 100                 # 45..175
write_config_file = true  # 是否同步写 user_settings.config
patch_code_clamp = false  # ★ 保持 false
```

改完**重启游戏**。

| fov | 观感 |
|---|---|
| 100 | 明显更宽，最保守 |
| **110–120** | 最舒服，日常推荐 |
| 130–140 | 广角，边缘开始拉伸 |
| 150–175 | 第一人称手臂变形明显，适合截图 |

下限 45 不会被改。

---

## 原理

设置存在**纯文本** `user_settings.config` 的 `vertical_fov` 里，但引擎在**读设置时**
会把值夹到 `[45, 90]`（实测：改成 130，退出后被写回 90，画面无变化）—— 所以只改文件没用。

夹取在设置反序列化里，长这样：

```asm
lea   rdx, "vertical_fov"
call  qword ptr [r8+0x88]     ; 读一个 float
movss xmm1, [45.0f]
comiss xmm1, xmm0
ja    +0x0c                   ; 下限：<45 取 45
movss xmm1, [90.0f]
minss xmm1, xmm0              ; ★ 上限：>90 取 90
```

但反序列化写的是**栈上临时结构**，真正被应用的是另一份持久对象：

```
vertical_fov 地址 = [[game.dll_base + 0x192B2D0] + 0xAB884] + 0x2C
```

**mod 写的是这一份** —— 这才是"本局就生效"的通路。

三个偏移来自 1.8.45317.0，所以做了三重防漂移，全部**拒写优先**：

1. **形状扫描**：按 `mov rcx,[rip+d32]; add rcx,imm32` 扫出所有候选，不写死 RVA。
2. **身份校验**：写前读那个 float，必须正好等于配置里那个（已被引擎夹过的）`vertical_fov`，
   否则拒写、只落盘诊断。字段偏移也按值反推，漂了照样找得到。
3. **持续维护**：引擎改设置时会重新序列化该结构，所以 `update` 钩子每 ~1.5 秒复查，
   被冲掉就补回（`reasserts` 计数）。稳态每次只读 4~8 字节，**只在状态实质变化时**才落盘。

启动时没定位到就在随后的帧里退避重试（8 秒一次、最多 5 次、90 秒封顶），
所以 **addon 加载顺序不影响结果**。

## 已验证

构建 `1.8.46015.0`，纯数据模式：

```
code_clamp      = disabled_by_config   ← game.dll 代码段未改动
patch_applied   = false
settings_phase  = applied
settings_before = 55.00  ->  wrote 130
settings_written= true
```

活设置对象每次启动地址都不同（ASLR），mod 每次重新定位。

---

## 已知限制

- **纯本地改动**，别人看不到你的视野。
- **联机有反作弊风险**（GameGuard 在跑），不建议公开联机使用，自担风险。
- **构建漂移**：签名反自 1.8.45317.0 的镜像，实机已是 1.8.46015.0。
  两套匹配都没中时会拒写并留下诊断文件（见下），据此可离线修正。
- 别和别的改 `game.dll` 代码的 mod 同时用：本 mod 只认自己校验过的那条指令，
  别人先改过就不写入（安全，但也不生效）。

## 还原

1. 管理器取消勾选 / Purge → 内存改动随进程消失。
2. 设置文件：`user_settings.config.fov_unlock.bak` 改回 `user_settings.config`。
3. 临时停手：`fov_unlock.cfg` 里 `enabled = false`。

---

## 出问题时要什么

1. `%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_UNLOCK_STATUS.txt`
2. `…\FOVUnlock.log`
3. `%APPDATA%\Arrowhead\Helldivers2\user_settings.config`
4. 夹取点没中时：`…\FOV_SIGNATURE_DUMP.txt`
5. 活设置对象没定位到时：`…\FOV_CHAIN_DUMP.txt`

第 4、5 个是**形状级**证据，不含写死地址：前者记录每个 `minss` 命中点的 RVA、前面 `movss`
引用的常量、`[-0x20,+0x10]` 原始字节（前 12 个无条件记录，换常量也有得看）；
后者记录每条 `mov rcx,[rip+d32]; add rcx,imm32` 命中的 site/global/struct_off/singleton
及结构体前 `0x40` 字节（指针读不出来也记录形状）。有它们就能**离线**把新构建算出来，
不用再开一次游戏。

`STATUS` 已包含：loader API 版本、`game.dll` 基址、夹取点 RVA 与命中来源
（strict/fallback）、校验读到的常量实际值、float 解码器自检、活设置对象地址/偏移/写入前后值、
重补与拒绝次数、`last_write_verdict`（下次启动判定引擎有没有把值改回去）。

---

## 开发

```powershell
python scripts\build_addon.py --source Source --output-dir Addon
python tests\test_fov_unlock.py --verbose          # 34 个用例，跑在 lupa.luajit21
python scripts\package.py
```

`tools/` 是离线分析用的：`pe_scan.py`（PE + RIP 引用扫描）、`disasm.py`（capstone）、
`find_clamp.py`（找 45/90 成对夹取）、`map_fields.py`（键名 → 结构偏移）、
`settings_map.py`、`probe_descriptor.py`、`check_release.py`（成品正文自检）。

几条硬性做法：

- **仿真用 LuaJIT 而非 CPython 自带的 Lua**：`lupa.luajit21` + `tests/lua51_syntax_check.lua`
  （后者用的 `lua51.dll` 就是游戏自己那个）。闸门带变异自测：把 `//` 放回去必须被抓住。
- **float 用纯 Lua 解 IEEE-754 并开机自检**：扫描器要在几十万条指令里反复读 float，
  `ffi.copy` + `float[1]` 的类型双关会被 LuaJIT 在热循环里缓存成陈旧值。
- **34 个用例里有 13 个断言"必须拒绝或必须留下证据"**：45 常量不对、缺 comiss/ja、
  签名不唯一、写入静默失败、身份校验不过、字段偏移漂移后写错地方、诊断没落盘、
  float 解码器被改坏、语法闸门被绕过等。
- **`.gitattributes` 里的 `eol=lf` 别删**：源码行尾直接进打包正文，
  而正文的 MurmurHash64A 是 loader 的查表键 —— CRLF 一转换，mod 会"突然不被发现"。

> 历史坑：v2.0.0 及更早的包因为源文件被写入 UTF-8 BOM，打包脚本又补了一行 marker，
> 导致正文第 2 行带 BOM、LuaJIT 解析失败 —— **mod 根本没加载**，表现却是"没生效"。
> 现在打包器直接拒绝 BOM 与重复声明，并在打包时**编译真实的归档正文**（只编译源文件
> 查不出来：`luaL_loadfile` 会跳过开头 BOM）。
