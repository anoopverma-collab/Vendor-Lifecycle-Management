# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Separate, later patch — seed_reboarding_request_email_templates_once
	# already ran on every existing site by the time these two creator-
	# facing templates were added, and editing that patch's own body would
	# have zero effect on a site that already migrated past it. Same
	# reasoning as every other one-time patch in this app.
	from vendor_lifecycle.vendor_lifecycle.install import (
		backfill_default_reboarding_request_creator_email_templates,
	)

	backfill_default_reboarding_request_creator_email_templates()
