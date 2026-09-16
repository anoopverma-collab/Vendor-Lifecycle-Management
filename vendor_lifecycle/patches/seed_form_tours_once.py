# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Same reasoning as the other patches in this file. Form Tour has no
	# committed JSON file backing it (unlike Module Onboarding/Onboarding
	# Step, which Frappe's own core file-sync keeps recreating regardless
	# of anything in install.py - those are left running on every migrate
	# on purpose), so it used to re-check and recreate itself on every
	# migrate the same way the master data did. Confirmed directly:
	# deleted a Form Tour, ran migrate, it came back. Now only ever runs
	# once - a deleted or customized tour actually stays that way.
	from vendor_lifecycle.vendor_lifecycle.install import (
		_install_sample_form_tours,
		backfill_form_tour_step_positions,
	)

	_install_sample_form_tours()
	backfill_form_tour_step_positions()
