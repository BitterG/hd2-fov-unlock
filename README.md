# FOV Unlock —— 绝地潜兵2 垂直视野解锁（突破 90 上限）

把 **Options → Visuals → Vertical Field of View（垂直视野）** 的硬上限 90 度去掉，
让你用 45–175 之间的任意值。

游戏本体滑条只给 45–90；这个 mod 改的是**引擎侧的夹取**，不是滑条。

> ## ✅ 实机已验证生效（v2.0.1，纯数据模式）
>
> 构建 `1.8.46015.0`，`mods/hd2/fov_unlock: loaded`：
>
> ```
> code_clamp      = disabled_by_config     ← game.dll 代码段未改动
> patch_applied   = false                  ← 通道① 全程没跑
> settings_phase  = applied
> settings_how    = scanned/known_off      ← 靠形状扫描定位，不依赖写死的 RVA
> settings_before = 55.00  ->  wrote 130
> settings_written= true
> config_written  = true
> ```
>
> 也就是说：**只靠"往活着的设置对象里写 4 个字节"，视野就变了，没有碰任何代码，
> 也没有触发 GameGuard。** 活设置对象每次启动地址都不同（ASLR，实测两次分别是
> `0x2005f5ec488` / `0x1335ec0c488`），mod 每次启动重新定位一次。

> ## ⚠️ 关于 GameGuard，先读这段
>
> **改 `game.dll` 的可执行页（`.text`）会触发 nProtect GameGuard，游戏直接关闭** ——
> 这是实测结论：**装了会改 `.text` 的 v1.x 之后出现，已确认是代码段补丁导致的**
> （本机 2026-09-24）。所以本 mod **默认是纯数据模式**：
>
> | 通道 | 默认 | 动作 | GG 风险 |
> |---|---|---|---|
> | ② 活设置对象（数据） | **开** | 往堆上的设置结构写 4 个字节 | 无（和其它 Bingus 数据类 mod 同性质） |
> | 写 `user_settings.config` | **开** | 改一个文本文件 | 无 |
> | ① 代码段上限补丁 | **关** | `VirtualProtect` 成 RWX + 改 `minss` 指令 | **会触发 GG、游戏被关掉** |
>
> 通道① 要在 `fov_unlock.cfg` 里写 `patch_code_clamp = true` 才打开，**风险自负**。
> 代码层面也上了约束：数据写入路径 `api.write` 一旦发现目标页带执行位就直接拒绝
> （`refusing_to_write_executable_page`），只有显式的 `api.write_code` 才会碰 `.text`。

> ## ⚠️ v2.0.0 及更早的一批包是坏的（已修，v2.0.1 起正常）
>
> 部署日志里是这样一行：
>
> ```
> mods/hd2/fov_unlock: load failed:
>   mods/hd2/fov_unlock.lua:39: '=' expected near 'local'
> ```
>
> 根因是**源文件被写入了 UTF-8 BOM**（`EF BB BF`）。打包脚本判断
> "首行是不是 `-- HD2-Addon:`" 时因为 BOM 而失败，于是**又补了一行 marker**，
> BOM 被留在正文第 2 行 —— LuaJIT 解析到就报错。
> 表现就是"装上了、日志里没报错、但什么都没发生"。
>
> 三道闸门现在都补上了：
> 1. `build_addon.py` 的 `envelope()` 遇到 BOM / 重复声明**直接报错退出**，不再容忍；
> 2. 打包时**编译真实的归档正文**（不是源文件）—— `luaL_loadfile` 会跳过开头 BOM，
>    所以只查源文件是查不出这个问题的；
> 3. 测试里加了 4 条回归（源文件无 BOM、打包器拒 BOM、拒重复声明、成品正文可编译）。

---

## 1. 装什么

| 需要 | 说明 |
|---|---|
| **Bingus Shared Loader v15 或更新** | **唯一的依赖。** 不需要再装第二个 mod。loader 日志首行会写 `Bingus Shared Loader loader-vNN; API 1`，那个 `API 1` 是 **loader 自己的 API 等级**，不是别的 mod。 |
| 本 mod 的 ZIP | 用 HDArsenal 或 HD2MM 导入，和 loader 一起启用，然后 Deploy。 |

装完看 `%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_UNLOCK_STATUS.txt`，
**第一行就是结论**（`OK - ...` / `FAILED - ...`）。

> **手动拷贝 `Addon\` 进 `data\` 的注意**：管理器会在部署时重排 `patch_N` 编号，
> 但手动拷会和别的 mod 互相覆盖（表现成“装了两个总有一个不生效”）。请用管理器。

---

## 2. 怎么配

把 `fov_unlock.cfg` 放到：

```
%APPDATA%\Arrowhead\Helldivers2\fov_unlock.cfg
```

```ini
enabled = true
fov = 100                 # 45..175，你要的垂直视野
write_config_file = true  # 是否把 fov 写进 user_settings.config
```

改完**重启游戏**。文件是可选的，没有它就用默认 `fov = 100`。

手感参考：100 已经明显更宽；110–120 是比较舒服的上限；超过 140 边缘拉伸很重。

---

## 3. 为什么“直接改配置文件”不管用

这两条都是在本机实测出来的，不是猜的：

1. 设置就存在**纯文本**里：
   `%APPDATA%\Arrowhead\Helldivers2\user_settings.config` → `vertical_fov = 55`
2. **把它改成 130，进游戏，退出后再看，文件被写回了 `vertical_fov = 90`**，
   而且画面没有变化。→ 引擎在**读设置时**就把值夹到 `[45, 90]` 了。

所以只改文件（或改完再用 mod 管理器覆盖）都不行，必须动引擎里那一次夹取。

---

## 4. 这个 mod 到底做了什么

### 4.1 定位

在 `game.dll` 里按**指令形状**找设置反序列化里针对 `vertical_fov` 的那段代码。
反汇编（本机 decrypted dump，`game.dll` 镜像）长这样：

```asm
movss xmm2, [rbx+0x2c]        ; 结构里的 vertical_fov，作为默认值
lea   rdx, "vertical_fov"     ; 键名
call  qword ptr [r8+0x88]     ; 从配置后端读一个 float
movss xmm1, [rip+…]           ; 45.0f
comiss xmm1, xmm0
ja    +0x0c                   ; 下限：<45 就取 45
movss xmm1, [rip+…]           ; 90.0f
minss xmm1, xmm0              ; ★ 上限：>90 就取 90
movss [rbx+0x2c], xmm1        ; 写回结构
```

### 4.2 校验（通道①，默认关闭）

按相对偏移匹配这 6 条，**所有位移都是通配的**（换构建、换 ASLR 基址都认得出）：

| 相对 `minss` | 期望 |
|---|---|
| `-0x15` | `F3 0F 10 0D` 且指向的 float **恰好是 45.0f** |
| `-0x0D` | `0F 2F C8`（comiss xmm1, xmm0） |
| `-0x0A` | `77 0C`（ja +0x0C） |
| `-0x08` | `F3 0F 10 0D` 且指向的 float **恰好是 90.0f** |
| `0x00` | `F3 0F 5D C8`（minss xmm1, xmm0） |

**必须 6 条全中才写入。** 只命中一部分的（比如某处别的 `minss 90`）会被记进
`STATUS` 的 notes 里并**拒绝写入** —— 宁可不动，也不要在不知道是什么地方乱写。

### 4.2b 兜底：按值锚定（构建漂移用）

严格匹配要求字节形状逐条吻合；编译器**换个寄存器分配**（`xmm1` → `xmm2`）就全不中。
所以严格版失败后会再跑一次**按值锚定**的匹配：

* 任一 `minss xmmR, xmmS`（寄存器形式，寄存器号不限）
* 它前面 `0x12` 字节内有一条 `movss`，指向的 float **恰好是 90.0f**
* 再往前 `0x30` 字节内有一条 `movss`，指向的 float **恰好是 45.0f**
* 两条 `movss` 之间有一条 `comiss` / `ucomiss`

补丁按 `minss` 自己的寄存器号生成 `movaps xmmR, xmmS`，所以寄存器变了也对。
**要求全进程唯一命中**，两条以上一律拒绝（`fallback_ambiguous`）。

命中来源会写进 `STATUS` 的 `clamp_via=strict|fallback`。

### 4.3 写入

把 `minss xmm1, xmm0`（`F3 0F 5D C8`）换成 `movaps xmm1, xmm0` + `nop`
（`0F 28 C8 90`）：`xmm1` 直接就是读进来的值，**上限消失，下限 45 保留**。

流程：读原始字节 → `VirtualProtect` 临时放开 → 写 → 恢复原保护属性 → **回读逐字节比对**。
回读不符就报告 `patch_applied = false`，不谎报成功。

注意 `90.0f` 这个常量在 `game.dll` 里被几百处共用，**所以绝不去改常量本身**
（那会把一堆无关的 90 度一起改掉），只改这一条指令。

### 4.4 配置

### 4.4 写侧：配置文件

同时把 `fov` 写进 `user_settings.config` 的 `vertical_fov`，
并**只备份一次**原始文件为 `user_settings.config.fov_unlock.bak`。

### 4.5 ★ 关键一步：写「活着的」设置对象

上面那条夹取补丁只管**以后**引擎再读设置的时候。但反序列化写的其实是一个**栈上的
临时结构**（boot 函数里 `lea rcx, [rbp+0x120]`）—— 那东西函数一返回就没了。
真正被应用的持久设置在别处。序列化器的调用点把它交出来了：

```asm
mov  rcx, qword ptr [rip+0xa70344]   ; 全局单例指针   (game.dll+0x192B2D0)
add  rcx, 0xab884                    ; 设置结构 = 单例 + 0xAB884
call <serializer>                    ; 序列化(设置)
```

而 `vertical_fov` 在这个结构里的偏移是 **+0x2C**（反序列化里
`movss xmm2,[rbx+0x2c]` 读默认值、`movss [rbx+0x2c],xmm1` 写回；序列化里
`movss xmm2,[rsi+0x2c]` 读出来写文件）。

于是本 mod 直接把那个字段改成你要的值：

```
vertical_fov 的真实地址 = [[game.dll_base + 0x192B2D0] + 0xAB884] + 0x2C
```

**这一条才是「本局就生效」的通路**，不再依赖引擎什么时候读设置。

安全性（三个偏移是 1.8.45317.0 的，所以必须防漂移）：

1. **身份校验**：写之前先读那个 float，必须**正好等于**配置快照里那个
   （已被引擎夹过的）`vertical_fov`（含其 clamp 形式），否则一律拒写、只落盘诊断。
2. **兜底定位**：不靠写死 RVA，而是在 `.text` 里按
   `48 8B 0D d32 48 81 C1 imm32`（`mov rcx,[rip+d]; add rcx,imm`）的形状扫出所有
   「单例 + 结构偏移」候选，取**唯一**通过身份校验的那个。
3. **持续性维护**：引擎一改设置就会重新序列化这个结构，所以一次性写不够。
   mod 用 `_G.update` 钩子每 ~1.5 秒复查一次，被冲掉就补回去
   （`reasserts` 计数写进 STATUS）。稳态下每次只读 4~8 字节，且**只在状态实质变化时**
   才落盘（技能 6.43 / 6.45）；退钩用带守卫的方式，不会截断别人的 update 链（技能 6.44）。

### 4.6 跨启动判定：引擎到底认不认我们写进去的值

你第一次实测已经证明了一件事：`vertical_fov` 改成 130，退出后文件里是 **90**。
那正好可以当探针用 —— mod 把「这次写了多少」记进
`%LOCALAPPDATA%\CowboyBingus\Helldivers2\fov_last_write.txt`，
下次启动时拿文件里读回的值对比，把结论写进 `STATUS` 的 `last_write_verdict`：

| 判定 | 含义 |
|---|---|
| `accepted: config still holds our value N` | 引擎接受了我们的值 → 上限通路成立 |
| `clamped back by the engine: we wrote N, next launch read M` | **引擎在启动时夹回了 M** → 那一趟上限补丁没赶在反序列化之前 |

所以你**不需要手动 diff 配置文件**：第二趟启动的 `STATUS` 会自己说清楚是哪一种。

另外，`STATUS` 第一行现在**不再声称"已解锁"**，它只说"已写入什么"。
mod 没有任何办法验证屏幕上到底变没变 —— 那句话得你来说。

---

## 5. 已知限制（请务必读）

* **本局就应该生效**：活设置对象那条通路（4.5）不依赖引擎何时读设置。
  万一 `STATUS` 第一行是 `PARTIAL`（只打上了内存上限补丁、没定位到活设置对象），
  才需要完全退出再进一次；那时请把 `settings_detail` 那行一起发出来。
* **别人看不到你的视野。** 这是纯本地内存改动。
* **联机有反作弊风险。** 游戏带 nProtect GameGuard；本地代码补丁属于有风险的动作，
  不建议在公开联机使用。自担风险。
* **构建漂移（这次是真实存在的）。** 本 mod 的签名是从一份 `game.dll` 内存镜像里
  反出来的，而那份镜像是 **1.8.45317.0**；你机器上的游戏在 **2026-09-24 18:50 更新到了
  `1.8.46015.0`**（`bin/helldivers2.exe` 的 FileVersion），中间还隔了一次 1.8.45850.0。
  → 这正是 4.2b 那个兜底匹配存在的原因。**如果两套匹配都没中，mod 会拒绝写入并留下
  `FOV_SIGNATURE_DUMP.txt`**（见第 7 节），我据此离线改签名，不用你再赌一轮。
* **别和别的改 `game.dll` 代码的 mod 一起用**——本 mod 只认自己校验过的那条指令，
  如果别人先改过同一条，校验就不会全中，本 mod 会拒绝写入（安全，但也不生效）。

---

## 6. 还原

1. mod 管理器里取消勾选 / Purge 本 mod → 内存补丁随进程消失，完全还原。
2. 设置文件：把 `user_settings.config.fov_unlock.bak` 改名回 `user_settings.config`
   （想保留 FOV 值就别动，或手动把 `vertical_fov` 改回 55~90）。

把 `fov_unlock.cfg` 的 `enabled` 改成 `false` 也能立刻停手（不写内存、不写设置文件）。

---

## 7. 出问题时要什么

只给这几样，我就能定位（缺一样基本等于从头再问）：

1. `%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_UNLOCK_STATUS.txt`
2. `%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOVUnlock.log`
3. `%APPDATA%\Arrowhead\Helldivers2\user_settings.config`
4. **夹取点没中时**：`%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_SIGNATURE_DUMP.txt`
5. **活设置对象没定位到时**：`%LOCALAPPDATA%\CowboyBingus\Helldivers2\FOV_CHAIN_DUMP.txt`

第 4、5 个是关键，两个都是**形状级**证据，不依赖任何写死的地址：

* `FOV_SIGNATURE_DUMP.txt`：每个 `minss` 命中点的 RVA、它前面 `movss` 引用的常量值、
  以及 `[-0x20, +0x10]` 的原始字节。**前 12 个 `minss` 无条件记录** —— 就算新构建把
  上下限常量都换了（45/90 都不在附近），也仍然有原始字节可以重新反推形状。
* `FOV_CHAIN_DUMP.txt`：每一条 `mov rcx,[rip+d32]; add rcx,imm32` 形状命中的
  `site_rva / global_rva / struct_off / singleton`，外加解出来的结构体前 `0x40` 字节的
  hex 与逐 float 值。**即使指针读不出来也会记下原始形状**，所以全局变量布局改了也照样
  有东西可看。

有了这两个文件，我就能**离线**把新构建的偏移/形状算出来，不用你再开一次游戏。

`STATUS` 里已经带了：loader API 版本、`game.dll` 基址、夹取点 RVA 与命中来源
（strict/fallback）、**校验时读到的两个常量实际值**、`minss` 总数、float 解码器自检结果、
活设置对象的地址/偏移/来历/写入前后值、重补次数、拒绝次数。

---

## 8. 证据与复现（给开发/审阅者）

本机用到的离线证据链：

| 文件 | 用途 |
|---|---|
| `FOV-Unlock/tools/pe_scan.py` | PE 解析 + UTF-16 字面量查找 + RIP 相对引用扫描 |
| `FOV-Unlock/tools/find_clamp.py` | 全 `.text` 扫 `minss/maxss/comiss` 指向 45.0f/90.0f 的点，找成对出现的夹取 |
| `FOV-Unlock/tools/disasm.py` | capstone 反汇编并解析 RIP 操作数的实际值 |
| `FOV-Unlock/tools/map_fields.py` | 从反序列化器里把「键名 → 结构体偏移」映射出来 |
| `FOV-Unlock/tools/settings_map.py` | 字符串表 / 函数起点 / 调用点查找 |

检索的镜像：`C:\Users\kugua\Desktop\Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll`
（解出来的 `game.dll` 内存镜像；本机 `data/game/game.dll` 带 WinLicense，静态读不出代码）。

判据：在 452 处 45.0f/90.0f 引用里，**只有一处 45 与 90 相距 0x0D**，
而且它正前方 31 字节就是 `lea rdx, "vertical_fov"`。这就是那个夹取。

### 测试

```powershell
python FOV-Unlock\tests\test_fov_unlock.py --verbose                  # 27 个用例，跑在 LuaJIT 上
work\host\lua51host.exe FOV-Unlock\tests\lua51_syntax_check.lua       # 用游戏自己的 lua51.dll 编译一遍
python FOV-Unlock\scripts\build_addon.py --source FOV-Unlock\Source --output-dir FOV-Unlock\Addon
python FOV-Unlock\scripts\package.py
```

27 个用例里有 13 个断言的是**必须拒绝、或必须留下证据**的路径：45 常量不对、缺 comiss/ja、没有签名、
写入静默失败、兜底命中不唯一、活设置对象身份校验不过、活设置对象不唯一、字段偏移漂移后写错地方、诊断文件没落盘、新构建换了常量后 dump 变空、
float 解码器被改坏、LuaJIT 语法闸门被绕过。
任何一条被误打，测试就红 —— 这就是这套测试的意义。

三条与技能对应的硬性做法：

* **仿真是 LuaJIT，不是 CPython 自带的那版 Lua**（技能 6.20）。
  `lupa.luajit21` + `work/host/lua51host.exe`；后者用的 `lua51.dll` 就是游戏自己那个
  （里面带 `LuaJIT` 字样），所以语法闸门跑在**游戏同款**虚拟机上。
  闸门本身还有一个变异自测：把 `//` 放回去，闸门必须抓住。
* **float 用纯 Lua 解 IEEE-754，并且开机自检**（技能 6.36）。
  扫描器要在几十万条指令里反复读 float，`ffi.copy` + `float[1]` 那种类型双关会被
  LuaJIT 在热循环里缓存成陈旧值。自检不过就 `phase = float_decoder_broken` 停手。
* **失败路径自带证据**（技能 6.19 / 6.30）：见第 7 节的第 4 个文件。

### 为什么不能离线验证当前构建（实测结论）

我试过：直接拿磁盘上的 `data/game/game.dll`（1.8.46015.0）来核签名。**不行**，
因为代码段是加密的（WinLicense）：

| 段 | raw 大小 | 熵 |
|---|---|---|
| (代码段 1) | `0x850800` | **8.000** |
| (数据段 2) | `0xc9a00` | **8.000** |
| `.boot` | `0x48d800` | **7.926** |
| `.edata` / `.rsrc` / `.tls` | 小 | 0.9 / 2.7 / 0.04（未加密） |

磁盘镜像里连 `vertical_fov` 字面量都找不到（ASCII 与 UTF-16 都是 0 命中），
clamp 形状与 chain 形状也都是 0 命中。**只有内存 dump 才能拿到真实代码** ——
这也是为什么 mod 要自己在失败时把形状落盘（第 7 节）。

### 本机构建对照

| | 版本 | 来源 |
|---|---|---|
| 反汇编用的 `game.dll` 镜像 | 1.8.45317.0 / build 24826606 | `Helldivers2-HD2-nProtect-Bypass-And-Dumper\game.dll_dump.dll`（2026-09-14） |
| 当前实机 | **1.8.46015.0** | `bin/helldivers2.exe` FileVersion，2026-09-24 18:50；`data/game/generated_*.dl_bin` 共 54 个 |

所以签名是**跨两个构建**用的，兜底匹配不是可选装饰，是必需品。
