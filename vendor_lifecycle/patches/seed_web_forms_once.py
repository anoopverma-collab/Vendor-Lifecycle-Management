# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt


def execute():
	# See the comment on sync_web_forms() in install.py — this used to run
	# unconditionally inside sync_standard_files() (called every single
	# migrate), which silently reverted any later customization made to
	# this app's Web Form(s) through the Desk's own visual editor. Now
	# only ever runs once — a site's own later customization actually
	# sticks.
	from vendor_lifecycle.vendor_lifecycle.install import sync_web_forms

	sync_web_forms()
