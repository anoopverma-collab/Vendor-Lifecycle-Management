# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

from vendor_lifecycle.setup import add_missing_supplier_status_options


def execute():
	# Existing sites: "Sign Off Failed" / "Re-Sign Off Failed" on Supplier's
	# Vendor Lifecycle Status. Fresh installs get them from after_install.
	add_missing_supplier_status_options()
