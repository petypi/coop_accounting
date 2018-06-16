import time
from itertools import groupby
from datetime import date
from odoo import models, _
from odoo.tools.misc import formatLang
from odoo.exceptions import UserError
from odoo.addons.web.controllers.main import clean_action
import logging

_logger = logging.getLogger("Debug")

class SupplierInvoiceFeedbackReport(models.AbstractModel):
    _name = "account.supplier.invoice.feedback"
    _description = "Supplier Invoice Feedback Report"
    _inherit = "account.report"

    filter_date = {"date_from": "", "date_to": "", "filter": "this_month"}
    filter_unfold_all = False

    def set_context(self, options):
        ctx = super(SupplierInvoiceFeedbackReport, self).set_context(options)
        ctx['strict_range'] = True
        return ctx

    def get_report_name(self):
        return _("Supplier Invoice Feedback Report")

    def get_column_names(self, options):
        pass

    def get_templates(self):
        templates = super(SupplierInvoiceFeedbackReport, self).get_templates()
        templates["line_template"] = "copia_accounting.line_template_supplier_invoice_feedback_report"
        templates["main_template"] = "copia_accounting.template_supplier_invoice_feedback_report"
        return templates

    def get_columns_name(self, options):
        return [
            {},
            {"name": _("Ref")},
            {"name": _("Source")},
            {"name": _("Inv. Status")},
            {"name": _("Delivered")},
            {"name": _("Received")},
            {"name": _("Status")},
            {"name": _("Invoice")},
            {"name": _("Total"), "class": "number"}
        ]

    def _get_query(self, options, line_id=None):
        result = []

        if line_id is None:
            _p_query = ""
        else:
            _p_query = "AND o.partner_id = {}".format(line_id.replace("partner_", ""))

        _query = """
            SELECT
                v.id vendor_id,
                v.name vendor_name,
                k.id picking_id,
                k.name picking_name,
                k.origin,
                o.invoice_status,
                k.create_date::DATE create_date,
                k.date date_delivery,
                k.date_done,
                k.state picking_state,
                ai.number invoice,
                coalesce(ai.amount_total, 0.0) total
            FROM stock_picking k
            JOIN purchase_order_stock_picking_rel rel
                ON rel.stock_picking_id = k.id
            JOIN purchase_order o
                ON o.id = rel.purchase_order_id
            JOIN res_partner v
                ON v.id = o.partner_id
            LEFT JOIN account_invoice_purchase_order_rel p_rel
                ON p_rel.purchase_order_id = rel.purchase_order_id
            LEFT JOIN account_invoice ai
                ON ai.id = p_rel.account_invoice_id
                AND k.name = split_part(ai.origin, ':', 1)
            WHERE k.date_done::DATE BETWEEN %s AND %s {}
            ORDER BY v.id ASC, k.date_done ASC;
        """.format(_p_query)

        self.env.cr.execute(_query, (
            options.get("date", {"date_from": date.today().__str__()})["date_from"],
            options.get("date", {"date_from": date.today().__str__()})["date_to"]
        ))
        res = self.env.cr.dictfetchall()

        for p, q in groupby(sorted(res, key=lambda k: k.get("vendor_id")), lambda k: k.get("vendor_id")):
            _g = [i for i in q]
            result.append(
                (p, {
                    "vendor_name": _g[0].get("vendor_name"),
                    "total": sum([j.get("total") for j in _g]),
                    "lines" : _g
                })
            )

        return result

    def get_lines(self, options, line_id=None):
        lines = []
        unfold_all = self.env.context.get('print_mode') and not options.get('unfolded_lines') or \
                     options.get('partner_id')

        result = self._get_query(options, line_id)
        total = 0.0
        for p, q in result:
            total += q["total"]
            lines.append({
                "id": "partner_{}".format(p),
                "name": q["vendor_name"],
                "columns": [{
                    "name": self.format_value(q["total"])
                }],
                "level": 2,
                "unfoldable": True,
                "unfolded": "partner_".format(p) in options.get('unfolded_lines') or unfold_all,
                "colspan": 8
            })
            if "partner_{}".format(p) in options.get("unfolded_lines") or unfold_all:
                too_many = False
                child_lines = []
                _lines = q["lines"]

                if _lines.__len__() > 80 and not self.env.get("print_mode"):
                    _lines = _lines[-80:]
                    too_many = True

                for l in _lines:
                    child_lines.append({
                        "id": l.get("picking_id"),
                        "parent_id": "partner_{}".format(p),
                        "name": l.get("create_date"),
                        "columns": [
                            {"name": l.get("picking_name")},
                            {"name": l.get("origin")},
                            {"name": l.get("invoice_status").title()},
                            {"name": l.get("date_delivery")},
                            {"name": l.get("date_done")},
                            {"name": l.get("picking_state").title()},
                            {"name": l.get("invoice")},
                            {"name": self.format_value(l.get("total"))}
                        ],
                        "level": 4
                    })

                if too_many:
                    child_lines.append({
                        "id": "too_many_{}".format(p),
                        "parent_id": "partner_{}".format(p),
                        "action": "view_too_many",
                        "action_id": "partner,%s" % (p,),
                        "name": _("There are more than 80 items in this list, click here to see all of them"),
                        "colspan": 8,
                        "columns": [{}],
                    })
                lines += child_lines

        if not line_id:
            lines.append({
                "id": "grouped_partners_total",
                "name": _("Total"),
                "level": 0,
                "class": "o_account_reports_domain_total",
                "columns": [
                    {"name": self.format_value(total)}
                ],
                "colspan": 8
            })

        return lines

    def open_picking(self, options, params):
        action = self.env.ref("stock.view_picking_form").read()[0]
        action["views"] = [(self.env.ref("stock.view_picking_form").id, "form")]
        action["res_id"] = int(params.get("id").split("_")[0])
        
        action = clean_action(action)
        return action
