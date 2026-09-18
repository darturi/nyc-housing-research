---
name: NYC Housing Research
description: A precise municipal research desk for local housing-law evidence.
colors:
  civic-ink: "#102439"
  deep-ink: "#071725"
  paper: "#f2f3ef"
  sheet: "#ffffff"
  layer: "#e8ebe9"
  rule: "#c5ccd0"
  rule-strong: "#8c99a2"
  body-ink: "#162331"
  muted-ink: "#596774"
  record-blue: "#1747d1"
  record-blue-hover: "#0d319e"
  signal-yellow: "#ffd332"
  network-orange: "#b8431a"
  local-green: "#13715f"
typography:
  headline:
    fontFamily: "Avenir Next, Avenir, Segoe UI, Helvetica, Arial, sans-serif"
    fontSize: "2rem"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Avenir Next, Avenir, Segoe UI, Helvetica, Arial, sans-serif"
    fontSize: "1.16rem"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Avenir Next, Avenir, Segoe UI, Helvetica, Arial, sans-serif"
    fontSize: "0.92rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Avenir Next, Avenir, Segoe UI, Helvetica, Arial, sans-serif"
    fontSize: "0.76rem"
    fontWeight: 800
    lineHeight: 1.4
    letterSpacing: "0.015em"
rounded:
  structural: "2px"
  chip: "999px"
spacing:
  tight: "8px"
  compact: "12px"
  section: "18px"
  panel: "24px"
  page: "36px"
components:
  button-primary:
    backgroundColor: "{colors.record-blue}"
    textColor: "{colors.sheet}"
    rounded: "{rounded.structural}"
    padding: "0 16px"
    height: "42px"
  button-primary-hover:
    backgroundColor: "{colors.record-blue-hover}"
    textColor: "{colors.sheet}"
  button-secondary:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.civic-ink}"
    rounded: "{rounded.structural}"
    padding: "0 16px"
    height: "42px"
  field:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.body-ink}"
    rounded: "{rounded.structural}"
    padding: "10px 11px"
    height: "42px"
  panel:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.body-ink}"
    rounded: "{rounded.structural}"
    padding: "24px"
---

# Design System: NYC Housing Research

## Overview

**Creative North Star: "The Municipal Research Desk"**

The interface behaves like a well-run public-records desk: compact, serious, easy to scan, and explicit about provenance. Its visual authority comes from disciplined rules, tabbed navigation, numbered evidence, and restrained civic color rather than institutional ornament.

The system is dense enough for repeat research without becoming cramped. White record sheets sit on a cool limestone field; midnight navigation anchors the workspace; blue marks primary action and current evidence; yellow is reserved for local status and active location. Long-form evidence remains calm and readable.

**Key Characteristics:**

- Flat, ruled surfaces instead of soft floating cards.
- Persistent distinction between local, networked, and potentially paid actions.
- Compact controls with familiar browser affordances.
- Evidence lists that privilege citation, excerpt, provenance, and official-source access.
- Responsive structure that becomes a horizontal workbench on narrow screens.

## Colors

The palette combines deep civic ink with cool record paper, one decisive action blue, and small semantic signals.

### Primary

- **Record Blue:** Primary actions, research emphasis, focus borders, and evidence numbering.
- **Civic Ink:** Desktop navigation, high-authority headings, and persistent application chrome.

### Secondary

- **Signal Yellow:** Active navigation markers, local-mode labels, and high-visibility keyboard focus.

### Tertiary

- **Network Orange:** Warnings and provider surfaces that may have an external or cost boundary.
- **Local Green:** Local/free state indicators and on-device privacy status.

### Neutral

- **Limestone Paper:** Application ground.
- **Record Sheet:** Forms, evidence surfaces, and panels.
- **Filed Layer:** Table headers, evidence headers, and secondary state fills.
- **Rule / Strong Rule:** Dividers, panel edges, and form-control boundaries.
- **Body Ink / Muted Ink:** Primary reading text and supporting metadata.

### Named Rules

**The Signal Ration Rule.** Yellow identifies location, local state, or keyboard focus; it is never scattered as decoration.

**The Boundary Rule.** Networked and potentially paid actions must carry a visible semantic label before the user acts.

## Typography

**Display Font:** Avenir Next with the shared workhorse fallback stack.

**Body Font:** Avenir Next with the same fallback stack.

**Character:** One sturdy sans family carries the entire product. Weight, scale, spacing, and tabular numerals create hierarchy without turning operational UI into a branded poster.

### Hierarchy

- **Headline:** Heavy, tightly tracked route titles; used once per view.
- **Title:** Compact panel and result headings with a clear one-step drop from route titles.
- **Body:** Regular-weight research copy; evidence excerpts stay near a 65–75 character measure where layout permits.
- **Label:** Heavy, compact field and metadata labels; uppercase is limited to small state chips.

### Named Rules

**The One-Workhorse Rule.** Operational screens use one sans family; hierarchy comes from weight and measured size changes, never a decorative second typeface.

## Layout

Desktop uses a 248px persistent navigation rail and a flexible workspace capped at 1500px. The primary research surface is a two-column records desk: evidence work on the left and a 310–360px HPD lookup on the right. Panels use compact 18–24px internal rhythm, with larger separation reserved for route changes.

At 1180px the HPD lookup joins the document flow. At 920px the rail becomes a horizontal navigation bar. At 700px forms and controls stack into one column; at 440px panels tighten to 18px padding. Type sizes remain fixed because this is an operating interface, while structure changes at breakpoints.

## Elevation & Depth

The system uses no shadows in the local workspace. Depth is expressed with tonal layers, one-pixel rules, dark application chrome, and occasional top rules that identify a panel's action boundary.

### Named Rules

**The Filed-Flat Rule.** A panel earns separation through ground, rule, and placement—not a border-plus-shadow stack.

## Shapes

Structural surfaces, controls, and buttons use near-square 2px corners. Fully rounded forms are reserved for small semantic chips and status labels. The brand mark is a precise four-cell square, and active navigation uses a narrow rectangular marker.

## Components

### Buttons

- **Shape:** Compact rectangular control with near-square corners and a 42px default height.
- **Primary:** Record Blue with white, heavy action text.
- **Hover / Focus:** Hover deepens to Record Blue Hover; focus receives the Signal Yellow ring and a blue border.
- **Secondary:** White with a strong neutral rule and Civic Ink text; hover fills with Filed Layer.
- **Disabled:** Filed Layer with muted text and no opacity reduction.

### Chips

- **Style:** Small outlined pills with uppercase semantic text.
- **State:** Green identifies local/free state, blue identifies network state, orange identifies potential charge, and yellow identifies local application mode.

### Cards / Containers

- **Corner Style:** Near-square 2px corners.
- **Background:** Record Sheet on Limestone Paper.
- **Shadow Strategy:** None.
- **Border:** One-pixel Rule, with a stronger top rule only when a panel owns a primary workflow boundary.
- **Internal Padding:** 20–24px on desktop and 18px on compact mobile.

### Inputs / Fields

- **Style:** White field, Strong Rule stroke, near-square corners, and compact 10px by 11px padding.
- **Focus:** Signal Yellow three-pixel outline with a blue border.
- **Error / Disabled:** Errors use a pale warm field and dark recovery text; disabled controls use Filed Layer and remain legible.

### Navigation

- The desktop rail uses Civic Ink, stacked route buttons, supporting descriptions, and a Signal Yellow active marker. Below 920px it becomes a horizontal bar; supporting descriptions disappear below 700px.

### Evidence Index

- Results begin with a Filed Layer header, then numbered passages separated by one-pixel rules. Citation and excerpt lead; provenance remains visible; expansion and official-source actions stay at the end of each passage.

### Product Guide

- The Guide route uses a numbered, ruled workflow instead of a collection of equal cards. Mode rows pair each task with its local, network, or potential-cost boundary, and direct actions return the user to the relevant operating view.

## Do's and Don'ts

### Do:

- **Do** make local, networked, and potentially paid actions visually distinguishable before activation.
- **Do** keep research excerpts readable, numbered, and attached to provenance.
- **Do** use rules and tonal layers to organize dense operational content.
- **Do** preserve native form-control behavior, visible focus, and responsive stacking.

### Don't:

- **Don't** turn every section into an equal rounded card or add decorative shadows.
- **Don't** spend Signal Yellow on decoration; it must communicate state or focus.
- **Don't** hide source metadata, legal caveats, cost boundaries, or network boundaries for visual cleanliness.
- **Don't** introduce decorative display type, ornamental motion, or unfamiliar controls into task-heavy views.
