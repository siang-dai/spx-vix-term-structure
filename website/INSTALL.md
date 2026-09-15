# Add the SPX page to `siang-dai.github.io`

1. Copy `spx.qmd` into the website repo root:

```bash
cp /path/to/spx-vix-term-structure/website/spx.qmd /path/to/siang-dai.github.io/spx.qmd
```

2. Append `styles_spx.css` to the existing `styles.css`:

```bash
cat /path/to/spx-vix-term-structure/website/styles_spx.css >> /path/to/siang-dai.github.io/styles.css
```

3. In `_quarto.yml`, add this after the TXO navbar item:

```yaml
      - href: spx.qmd
        text: SPX Observatory
```

So the relevant section becomes:

```yaml
      - href: txo.qmd
        text: TXO Observatory
      - href: spx.qmd
        text: SPX Observatory
```

4. Preview:

```bash
quarto preview
```

5. After the data repo has at least one successful run, commit the website:

```bash
git add spx.qmd _quarto.yml styles.css
git commit -m "feat: add SPX volatility observatory"
git push origin main
```
