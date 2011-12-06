from regluit.payment.parameters import *
from django.core.urlresolvers import reverse
from django.conf import settings
from regluit.payment.models import Transaction
from django.contrib.auth.models import User
from django.utils import simplejson as json
from django.utils.xmlutils import SimplerXMLGenerator
from django.db import IntegrityError
from django.db.models.query_utils import Q
from django.shortcuts import render_to_response
from django.template import RequestContext

import datetime
import dateutil.parser
import hashlib
import httplib
import traceback
import uuid
import os
import urllib
import urllib2
import logging
import random
import commands
import smtplib
import urlparse
import decimal
import sys

logger = logging.getLogger(__name__)

# transaction_type constants
IPN_TYPE_PAYMENT = 'Adaptive Payment PAY'
IPN_TYPE_ADJUSTMENT = 'Adjustment'
IPN_TYPE_PREAPPROVAL = 'Adaptive Payment PREAPPROVAL'

#pay API status constants
# I think 'NONE' is not something the API produces but is particular to our implementation
# couldn't we use the Python None?
IPN_PAY_STATUS_NONE = 'NONE'

# The following apply to INSTANT PAYMENTS
IPN_PAY_STATUS_CREATED = 'CREATED'
IPN_PAY_STATUS_COMPLETED = 'COMPLETED'
IPN_PAY_STATUS_INCOMPLETE = 'INCOMPLETE'
IPN_PAY_STATUS_ERROR = 'ERROR'
IPN_PAY_STATUS_REVERSALERROR = 'REVERSALERROR'
IPN_PAY_STATUS_PROCESSING = 'PROCESSING'
IPN_PAY_STATUS_PENDING = 'PENDING'

# particular to preapprovals -- may want to rename these constants to IPN_PREAPPROVAL_STATUS_*
IPN_PAY_STATUS_ACTIVE = "ACTIVE"
IPN_PAY_STATUS_CANCELED = "CANCELED"
IPN_PAY_STATUS_DEACTIVED = "DEACTIVED"

IPN_SENDER_STATUS_COMPLETED = 'COMPLETED'
IPN_SENDER_STATUS_PENDING = 'PENDING'
IPN_SENDER_STATUS_CREATED = 'CREATED'
IPN_SENDER_STATUS_PARTIALLY_REFUNDED = 'PARTIALLY_REFUNDED'
IPN_SENDER_STATUS_DENIED = 'DENIED'
IPN_SENDER_STATUS_PROCESSING = 'PROCESSING'
IPN_SENDER_STATUS_REVERSED = 'REVERSED'
IPN_SENDER_STATUS_REFUNDED = 'REFUNDED'
IPN_SENDER_STATUS_FAILED = 'FAILED'

# action_type constants
IPN_ACTION_TYPE_PAY = 'PAY'
IPN_ACTION_TYPE_CREATE = 'CREATE'

# individual sender transaction constants
IPN_TXN_STATUS_COMPLETED = 'Completed'
IPN_TXN_STATUS_PENDING = 'Pending'
IPN_TXN_STATUS_REFUNDED = 'Refunded'

# adaptive payment adjusted IPN reason codes
IPN_REASON_CODE_CHARGEBACK_SETTLEMENT = 'Chargeback Settlement'
IPN_REASON_CODE_ADMIN_REVERSAL = 'Admin reversal'
IPN_REASON_CODE_REFUND = 'Refund'

class PaypalError(RuntimeError):
    pass

class url_request( object ): 

  def __init__( self, base, url, data=None, headers={} ):
   
    conn = httplib.HTTPSConnection(base)
    conn.request("POST", url, data, headers)
    
    #Check the response - should be 200 OK.
    self.response = conn.getresponse()

  def content( self ):
    return self.response.read()

  def code( self ):
    return self.response.status


class Pay( object ):
  def __init__( self, transaction, return_url=None, cancel_url=None):
      
      headers = {
            'X-PAYPAL-SECURITY-USERID':settings.PAYPAL_USERNAME, 
            'X-PAYPAL-SECURITY-PASSWORD':settings.PAYPAL_PASSWORD, 
            'X-PAYPAL-SECURITY-SIGNATURE':settings.PAYPAL_SIGNATURE,
            'X-PAYPAL-APPLICATION-ID':settings.PAYPAL_APPID,
            'X-PAYPAL-REQUEST-DATA-FORMAT':'JSON',
            'X-PAYPAL-RESPONSE-DATA-FORMAT':'JSON'
            }

      if return_url is None:
        return_url = settings.BASE_URL + COMPLETE_URL
      if cancel_url is None:
        cancel_url = settings.BASE_URL + CANCEL_URL
        
      logger.info("Return URL: " + return_url)
      logger.info("Cancel URL: " + cancel_url)
      
      receiver_list = []
      receivers = transaction.receiver_set.all()
      
      if len(receivers) == 0:
          raise Exception
      
      # by setting primary_string of the first receiver to 'true', we are doing a Chained payment
      for r in receivers:
          if len(receivers) > 1:
              if r.primary:
                  primary_string = 'true'
              else:
                  primary_string = 'false'
                  
              receiver_list.append({'email':r.email,'amount':str(r.amount), 'primary':primary_string})
          else:
              receiver_list.append({'email':r.email,'amount':str(r.amount)})
                  
      logger.info(receiver_list)
        
      # actionType can be 'PAY', 'CREATE', or 'PAY_PRIMARY'
      # PAY_PRIMARY': "For chained payments only, specify this value to delay payments to the secondary receivers; only the payment to the primary receiver is processed"
      
      # feesPayer: SENDER, PRIMARYRECEIVER, EACHRECEIVER, SECONDARYONLY
      # if only one receiver, set to EACHRECEIVER, otherwise set to SECONDARYONLY
      
      if len(receivers) == 1:
        feesPayer = 'EACHRECEIVER'
      else:
        feesPayer = 'SECONDARYONLY'
      
      data = {
              'actionType': 'PAY',
              'receiverList': { 'receiver': receiver_list },
              'currencyCode': transaction.currency,
              'returnUrl': return_url,
              'cancelUrl': cancel_url,
              'requestEnvelope': { 'errorLanguage': 'en_US' },
              'ipnNotificationUrl': settings.BASE_URL + reverse('PayPalIPN'),
              'feesPayer': feesPayer,
              'trackingId': transaction.secret
              } 
      
      logging.info("paypal PAY data: %s" % data)
      print >> sys.stderr, "paypal PAY data:", data
      # Is ipnNotificationUrl being computed properly
      print >> sys.stderr, 'ipnNotificationUrl', settings.BASE_URL + reverse('PayPalIPN')
      
      # a Pay operation can be for a payment that goes through immediately or for setting up a preapproval.
      # transaction.reference is not null if it represents a preapproved payment, which has a preapprovalKey.
      if transaction.reference:
          data['preapprovalKey'] = transaction.reference
      
      self.raw_request = json.dumps(data)
   
      self.raw_response = url_request(settings.PAYPAL_ENDPOINT, "/AdaptivePayments/Pay", data=self.raw_request, headers=headers ).content() 
      print >> sys.stderr, "PAY request", settings.PAYPAL_ENDPOINT, "/AdaptivePayments/Pay", self.raw_request, headers 
      logger.info("paypal PAY response was: %s" % self.raw_response)
      print >> sys.stderr, "paypal PAY response was:", self.raw_response
      self.response = json.loads( self.raw_response )
      logger.info(self.response)
    
  def status( self ):
      if self.response.has_key( 'paymentExecStatus' ):
          return self.response['paymentExecStatus']
      else:
          return None 
  
  def error( self ):
      if self.response.has_key('error'):
          error = self.response['error']
          logger.info(error)
          return error[0]['message']
      else:
          return 'Paypal PAY: Unknown Error'
      
  def amount( self ):
    return decimal.Decimal(self.results[ 'payment_gross' ])
  
  def paykey( self ):
    return self.response['payKey']

  def next_url( self ):
    return '%s?cmd=_ap-payment&paykey=%s' % (settings.PAYPAL_PAYMENT_HOST, self.response['payKey'] )

  def embedded_url(self):
      return '%s/webapps/adaptivepayment/flow/pay?paykey=%s&expType=light'  % ( settings.PAYPAL_PAYMENT_HOST, self.response['payKey'] )

class PaymentDetails(object):
  def __init__(self, transaction=None):
 
      try:
          self.transaction = transaction
          self.errorMessage = None
          self.response = None
          
          headers = {
                'X-PAYPAL-SECURITY-USERID':settings.PAYPAL_USERNAME, 
                'X-PAYPAL-SECURITY-PASSWORD':settings.PAYPAL_PASSWORD, 
                'X-PAYPAL-SECURITY-SIGNATURE':settings.PAYPAL_SIGNATURE,
                'X-PAYPAL-APPLICATION-ID':settings.PAYPAL_APPID,
                'X-PAYPAL-REQUEST-DATA-FORMAT':'JSON',
                'X-PAYPAL-RESPONSE-DATA-FORMAT':'JSON'
                }
          
          # we can feed any of payKey, transactionId, and trackingId to identify transaction in question
          # I think we've been tracking payKey.  We might want to use our own trackingId (what's Transaction.secret for?)  
          data = {
                  'requestEnvelope': { 'errorLanguage': 'en_US' },
                  'trackingId':transaction.secret
                  }       
    
          self.raw_request = json.dumps(data)
       
          self.connection = url_request(settings.PAYPAL_ENDPOINT, "/AdaptivePayments/PaymentDetails", data=self.raw_request, headers=headers )
          self.raw_response = self.connection.content()
          self.code = self.connection.code()
          
          if self.code != 200:
            self.errorMessage = 'PayPal response code was %i' % self.code
            return
          
          logger.info("paypal PaymentDetails response was: %s" % self.raw_response)
          self.response = json.loads( self.raw_response )
          logger.info(self.response)
          
          self.status = self.response.get("status", None)
          self.trackingId = self.response.get("trackingId", None)
          self.feesPayer = self.response.get("feesPayer", None)
          payment_info_list = self.response.get("paymentInfoList", None)
          payment_info = payment_info_list.get("paymentInfo", None)
          
          self.transactions = []
          for payment in payment_info:
              receiver = {}
              receiver['status'] = payment.get("transactionStatus", None)
              receiver['txn_id'] = payment.get("transactionId")
              
              r = payment.get("receiver", None)
              if r:
                  receiver['email'] = r.get('email')
    
                  
              self.transactions.append(receiver)
              
      except:
          self.errorMessage = "Error: ServerError"
          traceback.print_exc()
          
      
  def error(self):
      if self.response and self.response.has_key('error'):
          error = self.response['error']
          logger.info(error)
          return error[0]['message']
      
      elif self.errorMessage:
          return self.errorMessage
      else:
          return None
              
  def compare(self):
    """compare current status information from what's in the current transaction object"""
    # I don't think we do anything with fundingtypeList, memo
    # status can be: 
    # transaction.type should be PAYMENT_TYPE_INSTANT
    # actionType can be: 'PAY', 'CREATE', 'PAY_PRIMARY' -- I think we are doing only 'PAY' right now

    comp = [(self.transaction.status, self.response.get('status')),
            (self.transaction.type, self.response.get('actionType')),
            (self.transaction.currency, self.response.get('currencyCode')),
            ('EACHRECEIVER' if len(self.transaction.receiver_set.all()) == 1 else 'SECONDARYONLY',self.response.get('feesPayer')),
            (self.transaction.reference, self.response.get('payKey')),  # payKey supposedly expires after 3 hours
            ('false', self.response.get('reverseAllParallelPaymentsOnError')),
            (None, self.response.get('sender'))
            ]
    
    # loop through recipients
    
    return comp
    
    # also getting sender / senderEmail info too here that we don't currently hold in transaction.  Want to save?  Does that info come in IPN?
    # responseEnvelope
    
    # reverseAllParallelPaymentsOnError
    # self.response.get('responseEnvelope')['ack'] should be 'Success' Can also be 'Failure', 'Warning', 'SuccessWithWarning', 'FailureWithWarning'
    # can possibly use self.response.get('responseEnvelope')['timestamp'] to update self.transaction.date_modified
    # preapprovalKey -- self.transaction doesn't hold that info right now
    # paymentInfoList -- holds info for each recipient
    

class CancelPreapproval(object):
    
    def __init__(self, transaction):
        
        headers = {
                 'X-PAYPAL-SECURITY-USERID':settings.PAYPAL_USERNAME, 
                 'X-PAYPAL-SECURITY-PASSWORD':settings.PAYPAL_PASSWORD, 
                 'X-PAYPAL-SECURITY-SIGNATURE':settings.PAYPAL_SIGNATURE,
                 'X-PAYPAL-APPLICATION-ID':settings.PAYPAL_APPID,
                 'X-PAYPAL-REQUEST-DATA-FORMAT':'JSON',
                 'X-PAYPAL-RESPONSE-DATA-FORMAT':'JSON',
                 }
      
        data = {
              'preapprovalKey':transaction.reference,
              'requestEnvelope': { 'errorLanguage': 'en_US' }
              } 

        self.raw_request = json.dumps(data)
        self.raw_response = url_request(settings.PAYPAL_ENDPOINT, "/AdaptivePayments/CancelPreapproval", data=self.raw_request, headers=headers ).content() 
        logger.info("paypal CANCEL PREAPPROBAL response was: %s" % self.raw_response)
        self.response = json.loads( self.raw_response )
        logger.info(self.response)
        
    def success(self):
        if self.status() == 'Success' or self.status() == "SuccessWithWarning":
            return True
        else:
            return False
        
    def error(self):
        if self.response.has_key('error'):
            error = self.response['error']
            logger.info(error)
            return error[0]['message']
        else:
            return 'Paypal Preapproval Cancel: Unknown Error'
        
    def status(self):
        if self.response.has_key( 'responseEnvelope' ) and self.response['responseEnvelope'].has_key( 'ack' ):
            return self.response['responseEnvelope']['ack']
        else:
            return None 
        

class Preapproval( object ):
  def __init__( self, transaction, amount, return_url=None, cancel_url=None):
      
      headers = {
                 'X-PAYPAL-SECURITY-USERID':settings.PAYPAL_USERNAME, 
                 'X-PAYPAL-SECURITY-PASSWORD':settings.PAYPAL_PASSWORD, 
                 'X-PAYPAL-SECURITY-SIGNATURE':settings.PAYPAL_SIGNATURE,
                 'X-PAYPAL-APPLICATION-ID':settings.PAYPAL_APPID,
                 'X-PAYPAL-REQUEST-DATA-FORMAT':'JSON',
                 'X-PAYPAL-RESPONSE-DATA-FORMAT':'JSON',
                 }

      if return_url is None:
        return_url = settings.BASE_URL + COMPLETE_URL
      if cancel_url is None:
        cancel_url = settings.BASE_URL + CANCEL_URL
      
      # set the expiration date for the preapproval
      now = datetime.datetime.utcnow()
      expiry = now + datetime.timedelta( days=PREAPPROVAL_PERIOD )  
      transaction.date_authorized = now
      transaction.date_expired = expiry
      transaction.save()
      
      data = {
              'endingDate': expiry.isoformat(),
              'startingDate': now.isoformat(),
              'maxTotalAmountOfAllPayments': '%.2f' % transaction.amount,
              'maxNumberOfPayments':1,
              'maxAmountPerPayment': '%.2f' % transaction.amount,
              'currencyCode': transaction.currency,
              'returnUrl': return_url,
              'cancelUrl': cancel_url,
              'requestEnvelope': { 'errorLanguage': 'en_US' },
              'ipnNotificationUrl': settings.BASE_URL + reverse('PayPalIPN')
              } 

      # Is ipnNotificationUrl being computed properly
      print >> sys.stderr, 'ipnNotificationUrl', settings.BASE_URL + reverse('PayPalIPN')
      
      self.raw_request = json.dumps(data)
      self.raw_response = url_request(settings.PAYPAL_ENDPOINT, "/AdaptivePayments/Preapproval", data=self.raw_request, headers=headers ).content() 
      logger.info("paypal PREAPPROVAL response was: %s" % self.raw_response)
      print >> sys.stderr, "paypal PREAPPROVAL response was:", self.raw_response
      self.response = json.loads( self.raw_response )
      logger.info(self.response)
      
  def paykey( self ):
    if self.response.has_key( 'preapprovalKey' ):
      return self.response['preapprovalKey']
    else:
      return None
  
  def next_url( self ):
    return '%s?cmd=_ap-preapproval&preapprovalkey=%s' % ( settings.PAYPAL_PAYMENT_HOST, self.response['preapprovalKey'] )

  def error( self ):
      if self.response.has_key('error'):
          error = self.response['error']
          logger.info(error)
          return error[0]['message']
      else:
          return 'Paypal Preapproval: Unknown Error'
      
  def status( self ):
    if self.response.has_key( 'responseEnvelope' ) and self.response['responseEnvelope'].has_key( 'ack' ):
      return self.response['responseEnvelope']['ack']
    else:
      return None 



class PreapprovalDetails(object):
  def __init__(self, transaction):
 
      try:
          self.transaction = transaction
          self.response = None
          self.errorMessage = None
          
          headers = {
                'X-PAYPAL-SECURITY-USERID':settings.PAYPAL_USERNAME, 
                'X-PAYPAL-SECURITY-PASSWORD':settings.PAYPAL_PASSWORD, 
                'X-PAYPAL-SECURITY-SIGNATURE':settings.PAYPAL_SIGNATURE,
                'X-PAYPAL-APPLICATION-ID':settings.PAYPAL_APPID,
                'X-PAYPAL-REQUEST-DATA-FORMAT':'JSON',
                'X-PAYPAL-RESPONSE-DATA-FORMAT':'JSON'
                }
          
          # we can feed any of payKey, transactionId, and trackingId to identify transaction in question
          # I think we've been tracking payKey.  We might want to use our own trackingId (what's Transaction.secret for?)  
          data = {
                  'requestEnvelope': { 'errorLanguage': 'en_US' },
                  'preapprovalKey':transaction.reference
                  }       
    
          self.raw_request = json.dumps(data)
       
          self.connection = url_request(settings.PAYPAL_ENDPOINT, "/AdaptivePayments/PreapprovalDetails", data=self.raw_request, headers=headers )
          self.raw_response = self.connection.content() 
          self.code = self.connection.code()
          
          if self.code != 200:
            self.errorMessage = 'PayPal response code was %i' % self.code
            return
          
          logger.info("paypal PreapprovalDetails response was: %s" % self.raw_response)
          self.response = json.loads( self.raw_response )
          logger.info(self.response)
          
          self.status = self.response.get("status", None)
          self.amount = self.response.get("maxTotalAmountOfAllPayments", None)
          self.currency = self.response.get("currencyCode", None)
          self.approved = self.response.get("approved", None)
          self.expiration = self.response.get("endingDate", None)
          self.date = self.response.get("startingDate", None)
          
      except:
          self.errorMessage = "Error: ServerError"
          traceback.print_exc()
      
  def error(self):
      if self.response and self.response.has_key('error'):
          error = self.response['error']
          logger.info(error)
          return error[0]['message']
      elif self.errorMessage:
          return self.errorMessage
      else:
          return None

class IPN( object ):
    
  def __init__( self, request ):
      
    try:
        # verify that the request is paypal's
        self.error = None
    
        url = "%s?cmd=_notify-validate" % settings.PAYPAL_PAYMENT_HOST
        data=urllib.urlencode(request.POST.copy())
        req = urllib2.Request(url, data)
        response = urllib2.urlopen(req)
        raw_response = response.read()
        status = response.code

        # check code
        if status != 200:
            self.error = 'PayPal response code was %i' % verify_response.code()
            return
  
        # check response
        if raw_response != 'VERIFIED':
            self.error = 'PayPal response was "%s"' % raw_response
            return

        # Process the details
        self.status = request.POST.get('status', None)
        self.sender_email = request.POST.get('sender_email', None)
        self.action_type = request.POST.get('action_type', None)
        self.pay_key = request.POST.get('pay_key', None)
        self.preapproval_key = request.POST.get('preapproval_key', None)
        self.transaction_type = request.POST.get('transaction_type', None)
        self.reason_code = request.POST.get('reason_code', None)
        self.trackingId = request.POST.get('tracking_id', None)
        
        self.process_transactions(request)
        
    except:
        self.error = "Error: ServerError"
        traceback.print_exc()

  def uniqueID(self):
      
      if self.trackingId:
          return self.trackingId
      else:
          return None
      
  def key(self):
        # We only keep one reference, either a prapproval key, or a pay key, for the transaction.  This avoids the 
        # race condition that may result if the IPN for an executed pre-approval(with both a pay key and preapproval key) is received
        # before we have time to store the pay key
        if self.preapproval_key:
            return self.preapproval_key
        elif self.pay_key:
            return self.pay_key
        else:
            return None
        
  def success( self ):
    return self.error == None

    
  @classmethod
  def slicedict(cls, d, s):
      return dict((str(k.replace(s, '', 1)), v) for k,v in d.iteritems() if k.startswith(s)) 
        
  def process_transactions(self, request):
      
      self.transactions = []
      
      transaction_nums = range(6)
      for transaction_num in transaction_nums:
          transdict = IPN.slicedict(request.POST, 'transaction[%s].' % transaction_num)
          if len(transdict) > 0:
              self.transactions.append(transdict)
              logger.info(transdict)
                
        
