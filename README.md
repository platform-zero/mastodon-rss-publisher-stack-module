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
