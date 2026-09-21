=== Protect Lassen Herbicide Tracker ===
Contributors: protectlassen
Tags: public records, pesticides, forestry, transparency
Requires at least: 6.4
Tested up to: 6.7
Requires PHP: 8.1
Stable tag: 0.1.0
License: GPLv2 or later

Publishes the Protect Lassen herbicide tracker on a WordPress site with real,
crawlable URLs.

== Description ==

This plugin renders the herbicide tracker inside your WordPress theme by
calling the tracker API server-side. It does not use an iframe, and it does not
copy tracker data into the WordPress database.

That matters for two reasons. An iframe has no URL of its own, so nothing
inside it can be linked, shared, indexed or cited — which defeats the purpose
of publishing a public record. And keeping a second copy of the data in
WordPress would create a second source of truth that immediately drifts out of
date.

Pages provided, all real URLs on your site:

* `/herbicide-tracker/`
* `/herbicide-tracker/{county}/`
* `/herbicide-application/{slug}/`
* `/chemical/` and `/chemical/{slug}/`
* `/herbicide-tracker-near-me/`

Each emits its own title, meta description, canonical link, OpenGraph tags and,
for application pages, schema.org Dataset structured data.

A shortcode, `[protect_lassen_tracker]`, embeds a filtered application grid in
any page or post.

== Installation ==

1. Upload the plugin folder to `/wp-content/plugins/` and activate it.
2. Go to Settings → Herbicide Tracker and enter the tracker API URL.
3. If the tracker URLs return 404, re-save Settings → Permalinks to flush
   rewrite rules.

== Privacy ==

Addresses entered into the "search near an address" form are passed to the
tracker API for that request only. They are not stored by this plugin and that
request is never cached.

== Changelog ==

= 0.1.0 =
* First release.
