# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def get_handler(hook_name, provider):
	"""Resolve a provider name (e.g. "Manual") to its handler instance, via
	the given hook registry. Mirrors the same registry pattern the `payments`
	app uses for Payment Gateway."""
	providers = frappe.get_hooks(hook_name) or {}
	handler_paths = providers.get(provider)
	if not handler_paths:
		frappe.throw(
			frappe._("No provider registered for {0} under {1}.").format(frappe.bold(provider), hook_name)
		)
	return frappe.get_attr(handler_paths[-1])()


def get_esign_handler(provider):
	return get_handler("vendor_lifecycle_esign_providers", provider)


def get_notification_handler(provider):
	return get_handler("vendor_lifecycle_notification_providers", provider)
