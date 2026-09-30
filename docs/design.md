# KPaper interface design

KPaper uses a restrained desktop reading workspace: neutral surfaces, fine borders and clear typography. The main action is solid and prominent; blue identifies selection, progress and keyboard focus.

The design adapts [Vercel Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines/blob/main/AGENTS.md) to SwiftUI and AppKit. Native controls retain their keyboard behavior and accessible labels.

## Layout

- Use a 32-point page inset, a 184-point navigation rail and consistent left edges.
- Page titles use 25-point semibold type. Supporting text stays quieter without becoming illegible.
- Keep forms and progress content scrollable at the 820 × 640 minimum window size.
- Put settings labels above their controls so long values have room.
- Truncate file identifiers in the middle; preserve the complete value in a tooltip.

## Controls and states

- Buttons have at least 36-point targets and descriptive names for icon actions.
- Use 6-point control corners and crisp borders. Focus adds a visible blue outline.
- Primary actions use a solid neutral fill; secondary actions use bordered surfaces.
- Avoid decorative gradients, bouncing buttons and inert elements styled like actions.
- Selection combines text weight and surface treatment, with color as an additional cue.

Validate the smallest supported window, ordinary laptop dimensions, long Korean titles, keyboard navigation and dark mode before shipping.
