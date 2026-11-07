# Image-generation prompt — FIxbot infrastructure

Use this with an image model (DALL·E, Midjourney, Imagen, Firefly, …) when you
want a **visual/illustrative** render rather than the accurate technical
diagrams in [`svg/`](svg/).

Two important rules:

1. **Do not trust the image model with labels.** It will misspell them. Generate
   the picture with short labels or no labels, then overlay the real text (or use
   the Graphviz poster as the labelled reference).
2. **Prefer image-to-image when possible.** Feed `png/poster.png` (layout/style)
   or `png/poster-detail.png` (exact topology) as the reference and use the prompt
   below only for style, palette, and rendering.
   Text-to-image models invent topology; a reference keeps the structure honest.

---

## Variant A — technical schematic poster (recommended)

**Aspect ratio:** 16:9 for slides; 1.414:1 (A2/A3 landscape) for print.
**Style:** modern technical illustration, clean vector, flat 2.5D isometric,
generous whitespace, thin strokes, subtle shadows, white or very light grey
background. No photorealism, no 3D render gloss, no stock-photo people.

**Prompt:**

> A clean, modern isometric technical diagram of a university AI study-assistant
> platform. White background, thin dark-grey outlines, flat vector style, calm
> professional palette: blue `#4A78B5` for shared services, green `#3E8E5A` for
> per-course runtimes, purple `#7B4FA6` for databases, amber `#E8A33D` for the
> edge, red `#B5485D` for external services and backups, yellow `#B7950B` for
> the Git content system.
>
> Layout, left to right, in four labelled zones:
>
> 1. **Users and edge (far left).** Three small icons: a student, a teacher, an
>    administrator. A single arrow passes through a small gateway labelled "DNS
>    + TLS" into a larger gateway block labelled "Ingress".
> 2. **Shared services (centre-left), one horizontal row of four blocks.**
>    "Public site", "Pipe Control" (a hub with ports `/sync`, `/repos`,
>    `/qkb-assets`), "Monitoring" (a dashboard with a small line chart and log
>    stack), and a cylindrical "Shared content volume" holding three folders:
>    system prompts, pipe bases, question banks.
> 3. **One Course (centre-right), a large rounded rectangle containing a row of
>    course cards.** Show three identical Course cards, each with an "Open WebUI"
>    application block, a purple cylinder labelled "Open WebUI DB", and a second
>    purple cylinder labelled "Pipe State DB". On one card, show two small dashed
>    optional modules labelled "Redis" and "Chroma". Between the cards and the
>    shared volume, draw a short dashed arrow labelled "content mounted".
> 4. **External systems and data (far right).** A yellow GitLab block with three
>    small repository icons; a red identity-provider block "OIDC"; a red "AIaaS"
>    block with a small neural-network glyph; a red cylindrical "S3 object
>    storage" at the bottom with two arrows coming from the database cylinders,
>    labelled "backups"; a small dashed "interim dump" cassette next to S3.
>
> Connections: a solid arrow from GitLab through a small validation gate into
> Pipe Control; a solid arrow from Pipe Control into the shared content volume;
> dashed arrows from the content volume into each Course card; solid arrows from
> each "Open WebUI" block to OIDC and AIaaS; solid arrows from each Open WebUI
> block to its two purple cylinders; dashed backup arrows from the cylinders to
> S3; dotted monitoring arrows from the running blocks to the Monitoring block.
>
> A small, uncluttered legend in the lower-left corner with four line samples:
> solid = request/data flow, dashed = optional or backup path, dotted =
> reconciliation, cylinder = database or persistent storage.
>
> Composition: balanced, highly legible at poster size, consistent icon grid,
> aligned rows, no overlapping labels, no clutter. Short sans-serif labels only;
> leave clean empty space around each label so text can be replaced later.

**Negative prompt:**

> photorealism, photographic people, glossy 3D render, glassmorphism, neon
> cyberpunk, dark background, purple gradient haze, circuit-board clichés,
> floating random icons, misspelled or gibberish text, dense unreadable
> micro-labels, wires crossing through boxes, distorted perspective, extra
> fingers, logos, watermarks, borders, busy decorative background.

---

## Variant B — conceptual hero illustration

Use when the image should communicate the *idea* rather than the topology (a
landing page, a talk opener, a report cover). Keep it text-free.

**Aspect ratio:** 16:9 or 3:2.
**Style:** editorial vector illustration, warm academic palette, flat shapes,
subtle paper texture, isometric or gently tilted perspective.

**Prompt:**

> An editorial vector illustration of a university course assistant as a
> knowledge-delivery system. On the left, teachers place curated question cards
> into a large, orderly archive building labelled only by a small git-branch
> glyph. The archive feeds a validation gate (a simple checkmark turnstile), then
> a central distribution hub — a clean, friendly control tower with pipes and
> conveyor belts. The hub sends glowing bundles along three parallel paths to
> three identical classroom pods, each containing a small AI assistant avatar and
> two stacked purple knowledge cylinders. The pods are connected to a small
> identity badge and a cloud-shaped model service. Below the floor, two protected
> vaults collect backup streams from the cylinders; a quiet control room with a
> line chart and log reels watches over the whole scene. Warm off-white
> background, muted blue-green-purple palette with amber accents, thin outlines,
> flat vector shading, generous negative space, no text.

Then overlay the real component names/callouts yourself if needed.

---

## Short prompt (for models with tight prompt limits)

> Clean isometric technical poster of a university AI study-assistant platform on
> Kubernetes: users enter through a DNS/TLS gateway into an ingress; shared
> services include a public site, a content-distribution hub, monitoring, and a
> shared content volume; three per-course pods each contain an Open WebUI app and
> two purple Postgres cylinders (application DB and state DB), with optional
> Redis and Chroma; a yellow GitLab block feeds validated content through the
> distribution hub into the shared volume and the course pods; a red OIDC
> provider and LLM API serve the pods; database backups flow to red S3 storage;
> dotted monitoring links. White background, flat vector, blue/green/purple/amber
> palette, thin outlines, short labels, legend bottom-left, no photorealism.

---

## Accuracy checklist (compare the generated image against this)

- [ ] One shared namespace / platform layer, separate from the Course stacks.
- [ ] Every Course has **two** databases: Open WebUI DB and Pipe State DB —
      not one shared database.
- [ ] Redis/Dragonfly and Chroma are visually **optional** (dashed), because
      they exist only for multi-replica Courses.
- [ ] Content travels **GitLab → validate → Pipe Control → propagate → shared
      volume → Courses**. It is not a direct GitLab-to-WebUI arrow.
- [ ] Backups go from both databases to object storage; the interim dump is a
      separate dashed safety net.
- [ ] OIDC and the LLM API are external services, not part of the cluster.
- [ ] Monitoring is self-hosted and observes the whole namespace.
- [ ] No secret values, tokens, or personal data appear in the image.

## Suggested workflow

1. Render the accurate labelled diagram: `./scripts/render-architecture.sh`.
2. Use `png/poster.png` as the style/layout reference, and
   `png/poster-detail.png` when you need the exact four-panel topology.
3. Generate with Variant A for a technical poster or Variant B for a hero image.
4. Overlay final labels from the Graphviz SVG in a vector editor (Inkscape,
   Figma) — do not re-type them into the image model.
