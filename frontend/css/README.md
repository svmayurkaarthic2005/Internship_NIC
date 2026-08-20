# CSS Architecture

This directory contains modular, well-organized stylesheets following modern CSS best practices.

## File Structure

```
css/
├── variables.css      # Design system variables (colors, spacing, typography)
├── global.css         # Global styles (reset, base elements, scrollbars)
├── animations.css     # All keyframe animations
├── components.css     # Reusable component styles (buttons, inputs, badges, etc.)
├── login.css          # Login page specific styles
├── chatbot.css        # Chatbot page specific styles
├── responsive.css     # Responsive design breakpoints
└── README.md          # This file
```

## Import Order

Files are imported in this specific order in HTML files:

1. **variables.css** - Must be first (defines all CSS variables)
2. **global.css** - Base styles and resets
3. **animations.css** - Keyframe animations
4. **components.css** - Reusable components
5. **login.css** or **chatbot.css** - Page-specific styles
6. **responsive.css** - Responsive overrides

This order ensures:
- Variables are available to all subsequent files
- Base styles apply before component styles
- Component styles don't override page-specific styles
- Responsive styles override everything (mobile-first approach)

## Design System

### Color Palette

```css
--primary: #1B4D9B          /* Main brand color */
--primary-dark: #163F82     /* Darker shade */
--primary-light: #EEF4FF    /* Lighter shade */

--success: #16A34A          /* Success/positive */
--warning: #F59E0B          /* Warnings */
--danger: #DC2626           /* Errors/danger */
--info: #1B4D9B             /* Info messages */

--background: #F8FAFC       /* Page background */
--surface: #FFFFFF          /* Cards, panels */
--border: #DCE3EC           /* Borders */
--text-primary: #1A202C     /* Main text */
--text-secondary: #64748B   /* Secondary text */
```

### Spacing Scale

```css
--spacing-xs: 4px
--spacing-sm: 8px
--spacing-md: 12px
--spacing-lg: 16px
--spacing-xl: 24px
--spacing-2xl: 32px
--spacing-3xl: 48px
--spacing-4xl: 60px
```

### Border Radius

```css
--radius-sm: 6px
--radius-md: 8px
--radius-lg: 12px
--radius-full: 50%
```

### Shadows

```css
--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05)
--shadow: 0 1px 3px rgba(0, 0, 0, 0.1)
--shadow-md: 0 4px 6px rgba(0, 0, 0, 0.1)
--shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1)
--shadow-xl: 0 20px 25px rgba(0, 0, 0, 0.1)
--shadow-2xl: 0 25px 50px rgba(0, 0, 0, 0.25)
```

## Naming Conventions

### CSS Classes

Use lowercase with hyphens:
```css
.btn-primary          /* Good */
.message-content      /* Good */
.chatHeaderNew        /* Bad */
.message_wrapper      /* Bad */
```

### Modifiers

Use double hyphens for modifiers:
```css
.btn--primary         /* Primary button variant */
.message--user        /* User message variant */
.message--assistant   /* Assistant message variant */
```

### States

Use descriptive state names:
```css
.is-active            /* Current/active state */
.is-disabled          /* Disabled state */
.is-loading           /* Loading state */
.has-error            /* Error state */
```

## Breakpoints

```css
/* Desktop (default) */

/* Tablets: 1024px and below */
@media (max-width: 1024px) { }

/* Medium screens: 768px and below */
@media (max-width: 768px) { }

/* Mobile: 480px and below */
@media (max-width: 480px) { }

/* Extra small: 320px and below */
@media (max-width: 320px) { }
```

## Component Examples

### Button

```html
<button class="btn-primary">Click me</button>
```

### Input

```html
<input type="text" class="input-field" placeholder="Enter text">
```

### Message

```html
<div class="message message-user">
    <div class="message-content-wrapper">
        <div class="message-bubble user">Hello!</div>
        <div class="message-footer">
            <span class="message-time">12:00 PM</span>
        </div>
    </div>
</div>
```

### Toast

```html
<div class="toast toast-success show">
    <svg class="toast-icon">...</svg>
    <span class="toast-message">Success!</span>
</div>
```

## Best Practices

1. **Use CSS variables** for colors, spacing, and typography
   ```css
   padding: var(--spacing-lg);
   color: var(--text-primary);
   ```

2. **Follow mobile-first approach** - add responsive overrides in responsive.css

3. **Use semantic class names** that describe content, not appearance
   ```css
   .message-content  /* Good */
   .blue-box         /* Bad */
   ```

4. **Group related properties**
   ```css
   /* Layout */
   display: flex;
   flex-direction: column;
   gap: var(--spacing-lg);
   
   /* Spacing */
   padding: var(--spacing-lg);
   margin: 0;
   
   /* Styling */
   background: var(--surface);
   border-radius: var(--radius-lg);
   ```

5. **Keep specificity low** - avoid nested selectors when possible

6. **Document complex styles** with comments

## Maintenance

When making CSS changes:

1. Update the appropriate file based on scope
2. Use existing variables from variables.css
3. Add responsive overrides to responsive.css (not inline media queries)
4. Test on multiple screen sizes
5. Run a production build to minify CSS

## Production Checklist

- [ ] All CSS is in modular files (no inline styles)
- [ ] No duplicate styles across files
- [ ] All variables are used consistently
- [ ] Responsive styles tested on actual devices
- [ ] Build process minifies CSS
- [ ] Unused CSS is removed
- [ ] Color contrast meets WCAG standards
- [ ] Animations respect `prefers-reduced-motion`
