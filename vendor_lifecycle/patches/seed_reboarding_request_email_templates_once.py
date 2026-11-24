# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Same reasoning as seed_email_templates_once.py - a separate, one-time
	# patch so a deliberately deleted or edited template actually stays
	# that way, rather than being silently recreated on every migrate.
	from vendor_lifecycle.vendor_lifecycle.install import backfill_default_reboarding_request_email_templates

	backfill_default_reboarding_request_email_templates()
