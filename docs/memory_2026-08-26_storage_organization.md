# Storage Organization & iCloud Optimization — Aug 26, 2026

## What Was Done

### Phase 1: iCloud Drive Organization
- Created structured hierarchy: Archive/, Projects/, Downloads/ with subfolders
- Moved 183.1, One82 Cursor, One82-go, One82Payments → Projects/
- Moved snapchat data → Archive/Exports/
- Moved Apple Media Services (both parts) → Archive/Apple Accounts/
- Moved root-level PDFs and images → Documents/ and Media/
- Sorted Downloads/ into Media/, Data/, Documents/ subfolders

### Phase 2: Local Home Directory Restructure
- Created `~/Projects/ai/` and `~/Projects/web/`
- Moved agent_system, jarvis, second_brain → Projects/ai/
- Moved kin, fireclock, family-lineage-builder-2 → Projects/web/
- Moved omnitest, paper_trading_bot, fireclock_backups → `~/_archive/`
- Created `~/_config/truenas/`, `~/_config/scripts/`, `~/_config/shell/`
- Moved all .truenas_* scripts from home root → `~/_config/truenas/`
- Moved .antigravity_organizer.sh, .disk2.sh, .disks.sh, .dodel.sh, sync_movies.sh → `~/_config/scripts/`
- Moved tarballs (agent_system.tar.gz, welift_sandbox.tar.gz), stray .py files → `~/_archive/scraps/`
- Moved 2014_Lexus_Packing_Guide.md, GS350_Packing_Calculator.html → ~/Documents/
- Cleaned home root to ~11 standard dotfiles only (no stray files)

### Phase 3: iCloud Sync Optimization
- Excluded node_modules from iCloud sync on 3 projects (183.1, One82 Cursor, One82Payments) — ~85k files
- Applied xattr: `com.apple.fileprovider.ignore#PS = 1`

### Phase 4: iCloud Backup of Critical Local Data
- Copied `~/_config/truenas/` (12 server configs) → iCloud Drive/Archive/Configs/truenas/
- Copied `~/scripts/` (watchdogs, health monitors) → iCloud Drive/Archive/Configs/scripts/
- Backed up dotfiles (.zshrc, .gitconfig, .zprofile, .profile) → iCloud Drive/Archive/Configs/
- Copied web projects (kin, fireclock, family-lineage-builder-2) → iCloud Drive/Projects/
- Copied archive scraps → iCloud Drive/Archive/scraps/
- Moved 35 Downloads images to iCloud Drive/Downloads/Documents/
- Moved 4 school CSVs to iCloud Drive/Documents/

## Resulting Structure

### Home Directory (~/)
```
~/Projects/ai/       ← agent_system, jarvis, second_brain, agent_system_dashboard, agent_system_remote
~/Projects/web/      ← kin, fireclock, family-lineage-builder-2
~/repos/             ← git repos (unchanged)
~/_archive/          ← omnitest, paper_trading_bot, fireclock_backups, scraps
~/_config/           ← truenas/ (server configs), scripts/ (automation), shell/ (dotfiles)
~/Media/             ← new hub (Movies, Pictures, Music symlinks)
~/Desktop/           ← iCloud synced
~/Documents/         ← iCloud synced
~/Downloads/         ← local (images backed up to iCloud)
```

### iCloud Drive
```
Archive/             ← Adobe, Apple Accounts, cars, Configs, dash cam, Exports, scraps, S&L Sports, etc.
Desktop/             ← Apple-managed sync
Documents/           ← Apple-managed sync (includes school, finance, packing guides, Obsidian)
Downloads/           ← Media/, Data/, Documents/ (sorted)
Media/               ← Edits, ScreenRecordings, Images, Music
Personal/            ← Gaming Laptop, Saint Anselm
Projects/            ← 183.1, One82 Cursor, One82-go, One82Payments, consolidated-app, kin, fireclock, family-lineage-builder-2
```

## Key Stats
- Disk: 12 GB used / 228 GB total / 67 GB free
- iCloud: 2 TB family plan, "Optimize Mac Storage" enabled
- node_modules excluded from sync: 3 projects, ~85k files
- Home root items: 8 directories + 11 dotfiles (was 30+ directories + 20+ stray files)
- iCloud Drive root: 8 clean items (was 22 cluttered)
- Nothing was deleted — all data preserved, just organized

## Philosophy Applied
- iCloud Drive → source code, documents, configs, exports
- Local only → cache files, build artifacts, node_modules, IDE data, app runtimes
- GitHub → active git repos
- Archives → preserved but out of the way
- Zero deletion — everything moved, nothing lost