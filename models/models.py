# -*- coding: utf-8 -*-
import re
import json
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)

class AccountPayment(models.Model):
    _inherit = "account.payment"

    ref = fields.Char("Reference No.", size=64)
    till_number = fields.Char(string="Mobile Money Till Number")
    mm_system = fields.Char(string="Mobile Money Platform")
    paid_by = fields.Many2one('res.partner')
    phone = fields.Char('Phone Number')

    def get_invoice_info_JSON(self):
        for record in self:
            if record.payment_type == 'outbound':
                query = '''
                select
                t5.number as invoice
                , t5.date_invoice
                , t5.amount_total as invoice_total
                , pp.previous_payments
                , (amount_total - pp.previous_payments) as previous_balance
                , t3.amount as paid_amount
                , (amount_total - pp.previous_payments - t3.amount) as invoice_balance
                from account_payment t1
                join account_move_line t2 on t2.payment_id = t1.id
                join account_partial_reconcile  t3 on t3.debit_move_id = t2.id
                join account_move_line t4 on t4.id = t3.credit_move_id
                join account_invoice t5 on t5.id = t4.invoice_id
                join
                LATERAL (select coalesce(sum(amount), 0.00) as previous_payments from account_partial_reconcile apr where apr.credit_move_id = t3.credit_move_id and apr.id < t3.id) pp on TRUE
                where t1.id = %s;
                '''
            else:
                query = '''
                select
                t5.number as invoice
                , t5.date_invoice
                , t5.amount_total as invoice_total
                , pp.previous_payments
                , (amount_total - pp.previous_payments) as previous_balance
                , t3.amount as paid_amount
                , (amount_total - pp.previous_payments - t3.amount) as invoice_balance
                from account_payment t1
                join account_move_line t2 on t2.payment_id = t1.id
                join account_partial_reconcile  t3 on t3.credit_move_id = t2.id
                join account_move_line t4 on t4.id = t3.debit_move_id
                join account_invoice t5 on t5.id = t4.invoice_id
                join
                LATERAL (select coalesce(sum(amount), 0.00) as previous_payments from account_partial_reconcile apr where apr.debit_move_id = t3.debit_move_id and apr.id < t3.id) pp on TRUE
                where t1.id = %s;
                '''
            #_logger.info('query is %s', query)
            res = self.env.cr.execute(query, (record.id,))
            #_logger.info('res is %s', res)
            data = self.env.cr.dictfetchall()
            #_logger.info('data is %s', data)
            record.invoices_formatted = data
            return data


    @api.constrains("ref")
    @api.one
    def _check_ref(self):
        if self.search_count([("ref", "=", self.ref), ("id", "!=", self.id)]):
            raise ValidationError(_(
                "Transaction Reference Number must be unique per Account Payment."
            ))

    @api.model
    def create(self, vals):
        if 'partner_id' in vals and 'paid_by' not in vals:
            vals.update({'paid_by' : vals.get('partner_id')})
        return super(AccountPayment, self).create(vals)
    
    @api.multi
    def confirm_payment_scheduler(self):
        voucher_ids = self.search([('partner_id.partner_type', '=', 'agent'), ('state', '=', 'draft'), ('partner_type','=', 'customer'), ('payment_type', '=', 'inbound'), ('amount', '>', 0.0)], order="payment_date")
        i = len(voucher_ids)
        for payment in voucher_ids:
            payment.post()
            i -= 1
            _logger.info('Confirm Payment Scheduler:-  Item Remaining -  (%s).', i)
            if not i % 30:
                self._cr.commit()
                
                

class AccountJournal(models.Model):
    _inherit = "account.journal"

    type = fields.Selection(selection_add=[
        ("sale_refund", "Sale Refund"), ("purchase_refund", "Purchase Refund"),
        ("situation", "Situation")
    ])


class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    vendor_number = fields.Char(
        "Vendor Invoice Number", copy=False, help="The Reference of this Invoice as provided by the Vendor."
    )
    origin = fields.Char(default="")

    @api.constrains("vendor_number")
    def _check_vendor_number(self):
        if self.search_count([("vendor_number", "=", self.vendor_number)]) > 1:
            raise ValidationError(_(
                "Vendor Invoice Number should be unique per Vendor Account Invoice !"
            ))

    @api.onchange('invoice_line_ids')
    def _onchange_origin(self):
        super(AccountInvoice, self)._onchange_origin()

        purchase_ids = self.invoice_line_ids.mapped("purchase_id")
        if purchase_ids:
            # Reset origin
            self.origin = ""
            self.origin = "#{}".format(", #".join(purchase_ids.mapped("name")))
            for p in purchase_ids:
                self.origin = re.sub(
                    "#{}".format(p.name), "{}:{}".format(", ".join(p.picking_ids.mapped("name")), p.name), self.origin
                )
    
    @api.multi
    @api.returns('self')
    def refund(self, date_invoice=None, date=None, description=None, journal_id=None):
        res = super(AccountInvoice, self).refund(date_invoice=date_invoice, date=date, description=description, journal_id=journal_id)
        context = dict(self._context or {})
        if context.get('line_account_id', False):
            res.invoice_line_ids.write({'account_id': context['line_account_id']})
        return res
    
    @api.multi
    def invoice_reconcile_scheduler(self):
        
        self.env.cr.execute("""
            select id from account_invoice where  
                state = 'open' and 
                type = 'out_invoice' and 
                partner_id in (
                    SELECT distinct partner_id FROM account_move_line WHERE 
                        full_reconcile_id is null  and  
                        credit > 0.0 and  
                        amount_residual < 0.0 and 
                        account_id in (
                            select id from account_account where 
                                user_type_id in (
                                    select id from account_account_type where 
                                    type = 'receivable'
                                    )
                            )
                    )  order by date_invoice asc;""")
        
        data = self.env.cr.dictfetchall()
        invoice_ids = [m['id'] for m in data]
        i = len(invoice_ids)
        for inv_id in invoice_ids:
            inv_obj = self.env['account.invoice'].browse(inv_id)
            credit_move_line_fetch_qry = """
                SELECT id FROM account_move_line WHERE full_reconcile_id is null  and  credit > 0.0 and 
                account_id =  %(invoice_account)s and partner_id = %(invoice_partner_id)s and amount_residual < 0.0
                order by date asc
                """%({
                  'invoice_account': inv_obj.account_id.id, 
                  'invoice_partner_id': inv_obj.partner_id.id,
                  })
            self.env.cr.execute(credit_move_line_fetch_qry)
            data = self.env.cr.dictfetchall()
            line_ids = [line['id'] for line in data]
            
            for move_line in line_ids:
                inv_obj.assign_outstanding_credit(move_line)
                if inv_obj.residual == 0.0:
                    break
            i -= 1
            _logger.info('Customer Invoice Reconciliation Scheduler:-  Item Remaining -  (%s).', i)
            if not i % 30:
                self._cr.commit()
    

class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    mpesa_ref = fields.Char(related="payment_id.ref", string="Mpesa Ref No.", size=64, store=True)
    till_no = fields.Char(related="payment_id.till_number", string="Till Number", store=True)

    @api.one
    @api.depends('invoice_id', 'partner_id', 'account_id')
    def _compute_company(self):
        self.company_id = self.account_id.company_id.id
         
     
    company_id = fields.Many2one('res.company', string='Company', store=True, compute='_compute_company')
    
    
    @api.one
    def remove_move_reconcile_record(self):
        """ Undo a reconciliation """
        if not self:
            return True
        for invoice in self.payment_id.invoice_ids:
            if self in invoice.payment_move_line_ids:
                self.payment_id.write({'invoice_ids': [(3, invoice.id, None)]})

        return True
    
    @api.onchange('amount_currency', 'currency_id')
    def _onchange_amount_currency(self):
        for line in self:
            amount = line.amount_currency
            if line.currency_id and line.currency_id != line.company_currency_id:
                amount = line.currency_id.with_context(date=line.date).compute(amount, line.company_currency_id)
            line.debit = amount > 0 and amount or 0.0
            line.credit = amount < 0 and -amount or 0.0

class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    @api.model
    def create(self, vals):
        res = super(AccountPartialReconcile, self).create(vals)

        if res.credit_move_id.payment_id and res.debit_move_id.invoice_id:
            if res.credit_move_id.payment_id.invoice_ids:
                for invoice in res.credit_move_id.payment_id.invoice_ids:
                    if res.debit_move_id not in invoice.payment_move_line_ids:
                        res.credit_move_id.payment_id.write({'invoice_ids': [(6, 0, [res.debit_move_id.invoice_id.id])]})
            else:
                res.credit_move_id.payment_id.write({'invoice_ids': [(6, 0, [res.debit_move_id.invoice_id.id])]})

        elif res.debit_move_id.payment_id and res.credit_move_id.invoice_id:
            if res.debit_move_id.payment_id.invoice_ids:
                for invoice in res.debit_move_id.payment_id.invoice_ids:
                    if res.credit_move_id not in invoice.payment_move_line_ids:
                        res.debit_move_id.payment_id.write({'invoice_ids': [(6, 0, [res.credit_move_id.invoice_id.id])]})
            else:
                res.debit_move_id.payment_id.write({'invoice_ids': [(6, 0, [res.credit_move_id.invoice_id.id])]})


        return res


    @api.multi
    def unlink(self):
        for rec in self:
            rec.debit_move_id.remove_move_reconcile_record()
            rec.credit_move_id.remove_move_reconcile_record()
        res = super(AccountPartialReconcile, self).unlink()
        return res

class AccountFinancialReportLine(models.Model):
    _inherit = "account.financial.html.report.line"
    
    @api.multi
    def get_lines(self, financial_report, currency_table, options, linesDicts):
        final_result_table = []
        comparison_table = [options.get('date')]
        comparison_table += options.get('comparison') and options['comparison'].get('periods', []) or []
        currency_precision = self.env.user.company_id.currency_id.rounding
        # build comparison table
        for line in self:
            res = []
            debit_credit = len(comparison_table) == 1
            domain_ids = {'line'}
            k = 0
            for period in comparison_table:
                date_from = period.get('date_from', False)
                date_to = period.get('date_to', False) or period.get('date', False)
                date_from, date_to, strict_range = line.with_context(date_from=date_from, date_to=date_to)._compute_date_range()
                r = line.with_context(date_from=date_from, date_to=date_to, strict_range=strict_range)._eval_formula(financial_report, debit_credit, currency_table, linesDicts[k])
                debit_credit = False
                res.append(r)
                domain_ids.update(r)
                k += 1
            res = line._put_columns_together(res, domain_ids)
            if line.hide_if_zero and all([float_is_zero(k, precision_rounding=currency_precision) for k in res['line']]):
                continue

            # Post-processing ; creating line dictionnary, building comparison, computing total for extended, formatting
            vals = {
                'id': line.id,
                'name': line.name,
                'level': line.level,
                'columns': [{'name': l} for l in res['line']],
                'unfoldable': len(domain_ids) > 1 and line.show_domain != 'always',
                'unfolded': line.id in options.get('unfolded_lines', []) or line.show_domain == 'always',
            }

            if line.action_id:
                vals['action_id'] = line.action_id.id
            domain_ids.remove('line')
            lines = [vals]
            groupby = line.groupby or 'aml'
            if line.id in options.get('unfolded_lines', []) or line.show_domain == 'always':
                if line.groupby:
                    domain_ids = sorted(list(domain_ids), key=lambda k: line._get_gb_name(k))
                for domain_id in domain_ids:
                    name = line._get_gb_name(domain_id)
                    vals = {
                        'id': domain_id,
                        'name': name and len(name) >= 45 and name[0:40] + '...' or name,
                        'level': 4,
                        'parent_id': line.id,
                        'columns': [{'name': l} for l in res[domain_id]],
                        'caret_options': groupby == 'account_id' and 'account.account' or groupby,
                    }
                    if line.financial_report_id.name == 'Aged Receivable':
                        vals['trust'] = self.env['res.partner'].browse([domain_id]).trust
                    lines.append(vals)
#                 if domain_ids:
#                     lines.append({
#                         'id': 'total_'+str(line.id),
#                         'name': _('Total') + ' ' + line.name,
#                         'class': 'o_account_reports_domain_total',
#                         'parent_id': line.id,
#                         'columns': copy.deepcopy(lines[0]['columns']),
#                     })

            for vals in lines:
                if len(comparison_table) == 2:
                    vals['columns'].append(line._build_cmp(vals['columns'][0]['name'], vals['columns'][1]['name']))
                    for i in [0, 1]:
                        vals['columns'][i] = line._format(vals['columns'][i])
                else:
                    vals['columns'] = [line._format(v) for v in vals['columns']]
                if not line.formulas:
                    vals['columns'] = [{'name': ''} for k in vals['columns']]

            if len(lines) == 1:
                new_lines = line.children_ids.get_lines(financial_report, currency_table, options, linesDicts)
                if new_lines and line.level > 0 and line.formulas:
                    divided_lines = self._divide_line(lines[0])
                    result = [divided_lines[0]] + new_lines + [divided_lines[1]]
                else:
                    result = []
                    if line.level > 0:
                        result += lines
                    result += new_lines
                    if line.level <= 0:
                        result += lines
            else:
                result = lines
            final_result_table += result

        return final_result_table