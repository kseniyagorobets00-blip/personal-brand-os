# Personal Brand OS Redesign Plan

## Direction

Personal Brand OS should feel like a premium editorial product for managing content, not a local prototype. It is both a personal content operating system and a product that can later be sold.

Design concept: Luxury Editorial OS.

## Design Inputs

- Overall feeling: premium editorial tool.
- Theme: soft graphite dark mode.
- Accent: coral / red, used sparingly.
- Density: balanced, workable but readable.
- Daily Brief priority: tasks for today first.
- Text workflow: Notion / Medium style editor, with optional Focus Mode.
- Visual character: luxury minimal.
- Product positioning: sellable product + personal content management system.
- Navigation: desktop sidebar; mobile bottom navigation or burger menu.
- Branding: restrained now, extensible later.
- Data-heavy pages: layout depends on the task.
- Effects: allowed only when they do not hurt readability.
- Redesign scope: bold structural rethink is allowed.
- First stage: bring the whole site into one coherent style.

## Product Structure

Group navigation by user intent:

- Today: Daily Brief.
- Planning: Content Plan, Texts, Ideas.
- Memory: Knowledge documents, cases, archive.
- Signals: Trend Radar.
- Settings: Author Profile, Rules, Editorial Strategy.

## Visual System

Palette:

- Background: deep graphite, near-black but warm enough for long reading.
- Panels: layered graphite surfaces with subtle contrast.
- Text: soft off-white and muted gray, avoid pure white.
- Accent: coral / red for primary actions, active states, urgent signals.
- Secondary states: muted graphite, slate, soft warm neutrals.

Principles:

- Fewer identical cards.
- Stronger hierarchy between primary work, context, and secondary details.
- More consistent vertical rhythm.
- Less boxed-in layout; use panels, lists, and detail views by purpose.
- No decorative overload, no gradient/orb backgrounds.
- Hover/focus states should feel polished but quiet.

## Layout Rules

Desktop:

- Persistent left sidebar.
- Page header with title, short context, and primary action.
- Main work area should have one obvious priority.
- Secondary explanation blocks should sit below or behind details.

Mobile:

- Compact navigation through bottom nav or burger menu.
- Avoid dense multi-column layouts.
- Primary action must remain reachable without scrolling too much.

## Page Concepts

### Daily Brief

Purpose: morning work screen.

First screen should show:

- tasks for today;
- what needs approval;
- what text is ready;
- next best action.

Secondary layers:

- why the system selected the topic;
- materials from memory;
- AI reasoning;
- trend context.

### Texts

Purpose: editorial workspace.

Main mode:

- list of posts;
- open post in clean editor;
- title, platform, date, status;
- large writing area;
- actions: save, approve, publish/archive.

Add Focus Mode:

- hide navigation and metadata;
- full-screen writing surface;
- minimal controls only.

### Content Plan

Purpose: planning and scheduling.

Use a planning layout:

- period controls;
- calendar or list view;
- compact publication rows;
- status and platform visible at a glance.

### Memory

Purpose: knowledge base.

Should feel like stored intelligence, not file storage:

- documents;
- cases;
- extracted themes;
- what AI understood;
- where the material is used.

### Author Profile

Purpose: control panel.

Use settings-style pages:

- author base;
- DNA;
- strategy;
- rules.

Each section should be compact by default, editable on demand.

### Trend Radar

Purpose: signals board.

Should show:

- strongest signals;
- why they matter;
- fit with Author Brain;
- possible publication angles.

## Implementation Phases

1. Design foundation:
   - tokens, colors, typography, spacing;
   - sidebar / mobile navigation;
   - shared page shell;
   - shared buttons, badges, panels, lists.

2. Convert global pages:
   - Daily Brief;
   - Texts;
   - Content Plan.

3. Convert knowledge and settings:
   - Memory;
   - Ideas;
   - Author Profile;
   - Rules.

4. Polish:
   - mobile layouts;
   - focus mode;
   - empty states;
   - hover/focus states;
   - accessibility and contrast.

## Non-Negotiables

- The product must not depend on one local machine.
- GitHub is the source of code truth.
- Render is the public app surface.
- Supabase is the memory/data sync layer.
- Local files are only a temporary working copy.
- Do not commit runtime data from `data/` unless explicitly requested.
