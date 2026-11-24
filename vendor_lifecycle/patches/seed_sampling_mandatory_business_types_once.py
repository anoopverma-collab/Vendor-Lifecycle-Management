# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Same reasoning as the other patches in this file - only ever
	# inserted a Business Type row that wasn't already present, which
	# meant a site that deliberately removed one (deciding Sampling
	# Evaluation shouldn't be mandatory for it anymore) got it silently
	# re-added on the very next migrate. Confirmed directly: removed
	# "Trader", ran migrate, it came back. Now only ever runs once.
	#
	# Must run after seed_master_data_once - it relies on default_
	# deboarding_rating_template/default_checklist_template already
	# being set (that patch sets them) for its own full settings.save();
	# patches.txt lists this one after it for exactly that reason.
	from vendor_lifecycle.vendor_lifecycle.install import backfill_sampling_mandatory_business_types

	backfill_sampling_mandatory_business_types()
