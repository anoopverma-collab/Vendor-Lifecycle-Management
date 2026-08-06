# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

from vendor_lifecycle.vendor_lifecycle.integrations.notification.base import BaseNotificationHandler


class ManualNotificationHandler(BaseNotificationHandler):
	"""No external channel configured — completion is only reflected on the
	checklist itself for a person to notice."""

	def notify(self, checklist):
		pass
