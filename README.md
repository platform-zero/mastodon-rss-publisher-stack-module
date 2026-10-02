# Mastodon RSS publisher stack module

Select the `mastodon-rss-publisher` module and the `mastodon-rss-publisher`
component to run the publisher service. Select any combination of the
`mastodon-rss-aus`, `mastodon-rss-tas`, `mastodon-rss-us`, and
`mastodon-rss-world` modules to materialize the default news core. China
independent coverage, China state media, and analysis are separately
selectable packs (`mastodon-rss-china-independent`,
`mastodon-rss-china-state`, and `mastodon-rss-analysis`) and are not part of
the default RSS/recommendation roster. No feed pack is selected by default.

The publisher creates one local `rss_` account per selected feed. Its first
successful fetch records current entries without posting them; later entries
are posted with the source attribution and canonical link. Every successfully
fetched dated item is retained in a rolling seven-day candidate bucket and
reported at `/state/calibration-report.json`; collection never posts history.
The Australian share is a calibration report for roster review, not a
continuously enforced posting quota.

## RSS account avatars

Each feed account has a versioned 512 × 512 PNG in
`stack.config/mastodon-rss/assets`. The image pairs its publisher's site icon
(where the publisher serves one) with the feed display name and a small `bot`
mark. `sources.json` records the publisher icon URL for each image; a null URL
means the publisher name is rendered as text. The observer gets its own image.
The account bootstrap updates an avatar when its image filename changes, so a
new image is applied to existing accounts on the next bootstrap run.

To regenerate the files after adding a feed or changing the artwork, run
`uv run --with pillow --with cairosvg python scripts/build-avatars.py` from a
workspace with the RSS feed modules checked out beside this module. Review the
resulting images before committing them. The script fetches icons from
publisher websites and places a `bot` watermark over each card.
