# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError


class AccountInvoiceRefund(models.TransientModel):
    _inherit = "account.invoice.refund"

    @api.model
    def _get_type(self):
        context = dict(self._context or {})
        active_id = context.get('active_id', False)
        if active_id:
            inv = self.env['account.invoice'].browse(active_id)
            return inv.type
        return ''
    
    type = fields.Selection([
            ('in_invoice', 'Vendor Bill'),
            ('out_invoice', 'Customer Invoice'),
        ], string='Type', default=_get_type)
    
    issue_on = fields.Selection([
            ('product_price', 'Product Price'),
            ('product_qty', 'Product Qty'),
        ], string='Issues On')
    
    account_id = fields.Many2one('account.account', string='Account',
        help="Use invoice line account.")
    
    
    @api.onchange('issue_on')
    def _onchange_issue_on(self):
        prod_categ = self.env.ref('product.product_category_all', False)
        for data in self:
            if data.issue_on == 'product_price':
                data.account_id = prod_categ.property_account_creditor_price_difference_categ and prod_categ.property_account_creditor_price_difference_categ.id or False
            elif data.issue_on == 'product_qty':
                data.account_id = prod_categ.property_stock_account_input_categ_id and prod_categ.property_stock_account_input_categ_id.id or False
            else:
                data.account_id = False
                
            
    
    @api.multi
    def compute_refund(self, mode='refund'):
        line_account_id = False
        for form in self:
            if form.type and form.type == 'in_invoice' and form.account_id:
                line_account_id = form.account_id.id
                break
        ctx = dict(self._context or {})
        if line_account_id:
            ctx = dict(self._context, line_account_id=line_account_id)
        return super(AccountInvoiceRefund, self.with_context(ctx)).compute_refund(mode=mode)
        
    
    
    
   
