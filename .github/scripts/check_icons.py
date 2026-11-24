"""CI check for the app's desk icon and navigation records.

Usage (from the bench's sites folder, with the bench's Python):
    python check_icons.py <site> installed
    python check_icons.py <site> uninstalled

"installed": the Desktop Icon, Workspace, Sidebar and Module Def exist, and
the icon files Frappe will ask for are on disk, linked into sites/assets and
listed in Frappe's desktop icon map (so the icon renders instead of a blank
tile). "uninstalled": none of those records is left behind.
"""

import os
import sys

import frappe

APP = "vendor_lifecycle"
MODULE = "Vendor Lifecycle"
LABEL = "Vendor Lifecycle"

# DocType -> filters that pick this app's records.
APP_RECORDS = {
	"Desktop Icon": {"app": APP},
	"Workspace": {"module": MODULE},
	"Workspace Sidebar": {"module": MODULE},
	"Sidebar": {"module": MODULE},
	"Module Def": {"app_name": APP},
	"Module Onboarding": {"module": MODULE},
}

errors = []


def check(ok, message):
	print(("OK    " if ok else "FAIL  ") + message)
	if not ok:
		errors.append(message)
		if os.environ.get("GITHUB_ACTIONS"):
			# Shows on the run's summary page as an annotation.
			print(f"::error title=check_icons::{message}")


def check_installed():
	for doctype, filters in APP_RECORDS.items():
		if frappe.db.exists("DocType", doctype):
			check(frappe.db.count(doctype, filters) > 0, f"{doctype} created")

	check(frappe.db.exists("Desktop Icon", {"label": LABEL, "app": APP}), f"Desktop Icon '{LABEL}' belongs to {APP}")

	from frappe.boot import get_desktop_icon_urls

	icon_urls = get_desktop_icon_urls().get(APP, {})
	for variant in ("subtle", "solid"):
		# Same path Frappe's desk builds from the icon label (utils.js get_desktop_icon).
		path = f"assets/{APP}/icons/desktop_icons/{variant}/{frappe.scrub(LABEL)}.svg"
		check(path in icon_urls.get(variant, []), f"{variant} icon listed by Frappe: {path}")
		check(os.path.isfile(os.path.join(frappe.local.sites_path, path)), f"{variant} icon served from sites/{path}")

	logo = frappe.get_hooks("app_logo_url", app_name=APP)
	logo = (logo[-1] if logo else "").split("?")[0]
	check(bool(logo), "app_logo_url set")
	if logo:
		check(os.path.isfile(os.path.join(frappe.local.sites_path, logo.lstrip("/"))), f"app logo served from sites{logo}")

	from frappe.apps import get_apps

	check(any(app["name"] == APP for app in get_apps()), "app shown on the Apps screen")


def check_uninstalled():
	check(APP not in frappe.get_installed_apps(), f"{APP} not in installed apps")
	for doctype, filters in APP_RECORDS.items():
		if frappe.db.exists("DocType", doctype):
			left = frappe.get_all(doctype, filters=filters, pluck="name")
			check(not left, f"no {doctype} left behind {left or ''}")
	left = frappe.get_all("Desktop Icon", filters={"label": LABEL}, pluck="name")
	check(not left, f"no Desktop Icon labelled '{LABEL}' left behind {left or ''}")


def main():
	site, mode = sys.argv[1], sys.argv[2]
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	try:
		{"installed": check_installed, "uninstalled": check_uninstalled}[mode]()
	finally:
		frappe.destroy()
	if errors:
		print(f"\n{len(errors)} check(s) failed")
		sys.exit(1)


if __name__ == "__main__":
	main()
