# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

from vendor_lifecycle.setup import grant_erpnext_permissions


def execute():
	# Existing sites: Vendor Lifecycle Manager / User access to Supplier,
	# Address, Contact, Bank Account and its masters. Fresh installs get it
	# from after_install.
	grant_erpnext_permissions()
