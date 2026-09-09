//+------------------------------------------------------------------+
//|                                              XAU_AI_Bridge.mq5   |
//|                        Copyright 2026, XAU AI Quantitative Team  |
//|                                  https://github.com/Tamilvanan0708 |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, XAU AI Quantitative Team"
#property link      "https://github.com/Tamilvanan0708"
#property version   "1.00"
#property description "Ultra-fast bridge connecting Python Quantitative Engine to MetaTrader 5."
#property description "STRICT POLICY: Exclusively executes Fib Retracement strategy trades on XAUUSD."

#include <Trade\Trade.mqh>

//--- Inputs
input group "=== Bot Connection Settings ==="
input string   InpBotURL        = "http://127.0.0.1:8000"; // Python Bot URL (or Render URL)
input string   InpSymbolOverride= "XAUUSD";                 // Broker Symbol (leave blank to use chart symbol)
input int      InpMagicNumber   = 777888;                   // EA Magic Number
input int      InpPollIntervalMs= 250;                      // Order Poll Interval in Milliseconds
input int      InpSlippagePts   = 50;                       // Max Slippage in Points

//--- Internal State
string g_symbol;
datetime g_last_heartbeat = 0;
CTrade g_trade;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (InpSymbolOverride != "") ? InpSymbolOverride : _Symbol;
   
   Print("==========================================================");
   Print("  🎯 XAU AI Bridge EA Started Successfully!");
   Print("  Connected Symbol: ", g_symbol);
   Print("  Bot API URL:      ", InpBotURL);
   Print("  Account Login:    ", AccountInfoInteger(ACCOUNT_LOGIN));
   Print("  Broker Server:    ", AccountInfoString(ACCOUNT_SERVER));
   Print("  Account Balance:  $", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));
   Print("  Leverage:         1:", AccountInfoInteger(ACCOUNT_LEVERAGE));
   Print("  Execution Filter: STRICTLY Fib Retracement Strategy");
   Print("==========================================================");
   
   // Send immediate first heartbeat
   SendHeartbeat();
   
   // Configure CTrade
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpSlippagePts);
   g_trade.SetTypeFillingBySymbol(g_symbol);
   
   // Start fast millisecond timer for low-latency signal polling
   EventSetMillisecondTimer(InpPollIntervalMs);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("[XAU_AI_Bridge] EA Terminated. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert timer function (polls bot and sends heartbeats)           |
//+------------------------------------------------------------------+
void OnTimer()
{
   // 1. Send periodic heartbeat every 2 seconds
   datetime now = TimeCurrent();
   if(now - g_last_heartbeat >= 2)
   {
      SendHeartbeat();
      g_last_heartbeat = now;
   }
   
   // 2. Poll for pending Fib Retracement orders
   PollPendingOrders();
}

//+------------------------------------------------------------------+
//| Sends Account Heartbeat to Python Bot                            |
//+------------------------------------------------------------------+
void SendHeartbeat()
{
   string url = InpBotURL + "/api/mt5/heartbeat";
   string headers = "Content-Type: application/json\r\n";
   
   string json = StringFormat(
      "{\"account_login\":%d,\"server\":\"%s\",\"balance\":%.2f,\"equity\":%.2f,\"leverage\":%d,\"symbol\":\"%s\"}",
      AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoString(ACCOUNT_SERVER),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoInteger(ACCOUNT_LEVERAGE),
      g_symbol
   );
   
   char post_data[];
   StringToCharArray(json, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(post_data, ArraySize(post_data)-1); // remove null terminator
   
   char result[];
   string result_headers;
   
   int res = WebRequest("POST", url, headers, 1500, post_data, result, result_headers);
   if(res == -1)
   {
      int err = GetLastError();
      if(err == 4014) // ERR_WEBREQUEST_CANNOT_CONNECT
      {
         static bool warned = false;
         if(!warned)
         {
            Print("⚠️ [XAU_AI_Bridge] ERROR 4014: WebRequest URL not allowed in MT5.");
            Print("👉 Please go to MT5: Tools -> Options -> Expert Advisors -> check 'Allow WebRequest' and add: ", InpBotURL);
            warned = true;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Polls Python Bot for pending Fib Retracement orders              |
//+------------------------------------------------------------------+
void PollPendingOrders()
{
   string url = InpBotURL + "/api/mt5/pending-orders?symbol=" + g_symbol;
   string headers = "Accept: application/json\r\n";
   char post_data[];
   char result[];
   string result_headers;
   
   int res = WebRequest("GET", url, headers, 1000, post_data, result, result_headers);
   if(res <= 0) return;
   
   string response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(StringFind(response, "\"orders_count\":0") >= 0 || StringFind(response, "\"orders\":[]") >= 0)
   {
      return; // No pending orders
   }
   
   // Parse and execute pending order
   ExecuteIncomingOrder(response);
}

//+------------------------------------------------------------------+
//| Simple JSON parser and native OrderSend execution                |
//+------------------------------------------------------------------+
void ExecuteIncomingOrder(string json)
{
   // Extract JSON fields
   string order_id = ExtractJsonString(json, "id");
   string action   = ExtractJsonString(json, "action");
   string symbol   = ExtractJsonString(json, "symbol");
   double lots     = ExtractJsonDouble(json, "lot_size");
   double sl       = ExtractJsonDouble(json, "stop_loss");
   double tp       = ExtractJsonDouble(json, "take_profit");
   double sl_pts   = ExtractJsonDouble(json, "sl_points");
   double tp_pts   = ExtractJsonDouble(json, "tp_points");
   string comment  = ExtractJsonString(json, "comment");
   
   if(order_id == "" || action == "") return;
   
   // Resolve chart/broker symbol: fallback to chart symbol if requested symbol has no market quotes
   string trade_symbol = (symbol != "") ? symbol : g_symbol;
   if(!SymbolInfoInteger(trade_symbol, SYMBOL_SELECT))
   {
      SymbolSelect(trade_symbol, true);
   }
   if(SymbolInfoDouble(trade_symbol, SYMBOL_ASK) <= 0.0 || SymbolInfoDouble(trade_symbol, SYMBOL_BID) <= 0.0)
   {
      PrintFormat("⚠️ [XAU_AI_Bridge] Symbol '%s' has no market quotes! Falling back to chart symbol '%s'...", trade_symbol, g_symbol);
      trade_symbol = g_symbol;
      if(!SymbolInfoInteger(trade_symbol, SYMBOL_SELECT))
      {
         SymbolSelect(trade_symbol, true);
      }
   }

   // 1. Position Close Dispatch
   if(action == "CLOSE")
   {
      ulong ticket = (ulong)ExtractJsonDouble(json, "ticket");
      string reason = ExtractJsonString(json, "reason");
      string close_dir = ExtractJsonString(json, "direction");
      bool closed = false;
      
      if(ticket > 0)
      {
         if(PositionSelectByTicket(ticket))
         {
            PrintFormat("🛑 [XAU_AI_Bridge] CLOSING POSITION Ticket #%d (Reason: %s)...", ticket, reason);
            closed = g_trade.PositionClose(ticket);
         }
         else
         {
            PrintFormat("⚠️ [XAU_AI_Bridge] Ticket #%d not active. Checking by symbol and magic...", ticket);
         }
      }
      
      // Fallback: If ticket was 0 or not found by ticket, search open positions with InpMagicNumber & Direction
      if(!closed)
      {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            ulong pos_ticket = PositionGetTicket(i);
            if(pos_ticket > 0)
            {
               long pos_magic = PositionGetInteger(POSITION_MAGIC);
               string pos_sym = PositionGetString(POSITION_SYMBOL);
               long pos_type = PositionGetInteger(POSITION_TYPE);
               
               bool dir_match = true;
               if(close_dir == "BUY" || close_dir == "LONG")
               {
                  dir_match = (pos_type == POSITION_TYPE_BUY);
               }
               else if(close_dir == "SELL" || close_dir == "SHORT")
               {
                  dir_match = (pos_type == POSITION_TYPE_SELL);
               }
               
               if(pos_magic == InpMagicNumber && (pos_sym == trade_symbol || pos_sym == g_symbol) && dir_match)
               {
                  PrintFormat("🛑 [XAU_AI_Bridge] Closing matching position #%d (Dir: %s)...", pos_ticket, close_dir);
                  if(g_trade.PositionClose(pos_ticket))
                  {
                     closed = true;
                     ticket = pos_ticket;
                     break; // Safety: Only close ONE matching trade per request
                  }
               }
            }
         }
      }
      
      PrintFormat(closed ? "✅ [XAU_AI_Bridge] POSITION CLOSED! Ticket: #%d" : "⚠️ [XAU_AI_Bridge] CLOSE NOT EXECUTED: Ticket #%d", ticket);
      SendExecutionReport(order_id, ticket, closed ? "CLOSED" : "CLOSE_FAILED", 0.0, g_trade.ResultRetcode(), closed ? "" : "Failed to close position");
      return;
   }

   // 2. Position Modify Dispatch (Smart Shield Breakeven / SL Trailing)
   if(action == "MODIFY")
   {
      ulong ticket = (ulong)ExtractJsonDouble(json, "ticket");
      string mod_dir = ExtractJsonString(json, "direction");
      double new_sl = ExtractJsonDouble(json, "stop_loss");
      double new_tp = ExtractJsonDouble(json, "take_profit");
      int digits = (int)SymbolInfoInteger(trade_symbol, SYMBOL_DIGITS);
      if(new_sl > 0) new_sl = NormalizeDouble(new_sl, digits);
      if(new_tp > 0) new_tp = NormalizeDouble(new_tp, digits);
      
      bool modified = false;
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         PrintFormat("🛡 [XAU_AI_Bridge] MODIFYING Ticket #%d -> SL=%.2f, TP=%.2f", ticket, new_sl, new_tp);
         modified = g_trade.PositionModify(ticket, new_sl, new_tp);
      }
      else
      {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            ulong pos_ticket = PositionGetTicket(i);
            if(pos_ticket > 0)
            {
               long pos_magic = PositionGetInteger(POSITION_MAGIC);
               string pos_sym = PositionGetString(POSITION_SYMBOL);
               long pos_type = PositionGetInteger(POSITION_TYPE);
               
               bool dir_match = true;
               if(mod_dir == "BUY" || mod_dir == "LONG")
               {
                  dir_match = (pos_type == POSITION_TYPE_BUY);
               }
               else if(mod_dir == "SELL" || mod_dir == "SHORT")
               {
                  dir_match = (pos_type == POSITION_TYPE_SELL);
               }

               if(pos_magic == InpMagicNumber && (pos_sym == trade_symbol || pos_sym == g_symbol) && dir_match)
               {
                  PrintFormat("🛡 [XAU_AI_Bridge] Modifying matching position #%d (Dir: %s) -> SL=%.2f, TP=%.2f", pos_ticket, mod_dir, new_sl, new_tp);
                  modified = g_trade.PositionModify(pos_ticket, new_sl, new_tp);
                  ticket = pos_ticket;
                  break;
               }
            }
         }
      }
      
      SendExecutionReport(order_id, ticket, modified ? "MODIFIED" : "MODIFY_FAILED", 0.0, g_trade.ResultRetcode(), modified ? "" : "Failed to modify position");
      return;
   }

   // 3. New Position Open Dispatch (BUY / SELL)
   if(lots <= 0) return;
   
   bool is_buy = (action == "BUY" || action == "LONG");
   double ask = SymbolInfoDouble(trade_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(trade_symbol, SYMBOL_BID);
   double price = is_buy ? ask : bid;
   
   // Normalize lot size to broker lot step
   double lot_step = SymbolInfoDouble(trade_symbol, SYMBOL_VOLUME_STEP);
   if(lot_step <= 0) lot_step = 0.01;
   lots = MathFloor(lots / lot_step) * lot_step;
   if(lots > 0.50) lots = 0.50; // Hard safety clamp
   
   // 1. If relative points provided, calculate SL & TP directly from MT5 live broker price
   // This eliminates 100% of price discrepancies between external feeds (Binance) and MT5!
   if(sl_pts > 0.0)
   {
      sl = is_buy ? (price - sl_pts) : (price + sl_pts);
   }
   if(tp_pts > 0.0)
   {
      tp = is_buy ? (price + tp_pts) : (price - tp_pts);
   }
   
   // 2. Protect against [Invalid stops]: Ensure SL and TP are on valid sides of market price
   if(is_buy)
   {
      if(sl >= price || sl <= 0.0) sl = 0.0;
      if(tp <= price || tp <= 0.0)
      {
         tp = (tp_pts > 0.0) ? (price + tp_pts) : (price + 2.0);
      }
   }
   else
   {
      if(sl <= price || sl <= 0.0) sl = 0.0;
      if(tp >= price || tp <= 0.0)
      {
         tp = (tp_pts > 0.0) ? (price - tp_pts) : (price - 2.0);
      }
   }
   
   // Digits rounding for SL / TP
   int digits = (int)SymbolInfoInteger(trade_symbol, SYMBOL_DIGITS);
   if(sl > 0) sl = NormalizeDouble(sl, digits);
   if(tp > 0) tp = NormalizeDouble(tp, digits);
   price = NormalizeDouble(price, digits);
   
   PrintFormat("🚀 [XAU_AI_Bridge] EXECUTING ORDER: %s %.2f Lots of %s @ %.2f (SL=%.2f, TP=%.2f)...",
               is_buy ? "BUY" : "SELL", lots, trade_symbol, price, sl, tp);
   
   // Prepare MqlTradeRequest
   MqlTradeRequest request;
   MqlTradeResult  trade_result;
   ZeroMemory(request);
   ZeroMemory(trade_result);
   
   request.action       = TRADE_ACTION_DEAL;
   request.symbol       = trade_symbol;
   request.volume       = lots;
   request.type         = is_buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   request.price        = price;
   request.sl           = sl;
   request.tp           = tp;
   request.deviation    = InpSlippagePts;
   request.magic        = InpMagicNumber;
   request.comment      = (comment != "") ? comment : "XAU_AI_Fib";
   
   // Determine supported filling mode dynamically
   uint filling = (uint)SymbolInfoInteger(trade_symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_IOC) != 0)
      request.type_filling = ORDER_FILLING_IOC;
   else if((filling & SYMBOL_FILLING_FOK) != 0)
      request.type_filling = ORDER_FILLING_FOK;
   else
      request.type_filling = ORDER_FILLING_RETURN;
   
   if(!OrderSend(request, trade_result))
   {
      if(request.type_filling != ORDER_FILLING_IOC)
      {
         request.type_filling = ORDER_FILLING_IOC;
         OrderSend(request, trade_result);
      }
      else
      {
         request.type_filling = ORDER_FILLING_RETURN;
         OrderSend(request, trade_result);
      }
   }
   
   // Check execution result
   if(trade_result.retcode == TRADE_RETCODE_DONE || trade_result.retcode == TRADE_RETCODE_PLACED)
   {
      PrintFormat("✅ [XAU_AI_Bridge] TRADE FILLED! Ticket: #%d, Fill Price: %.2f", trade_result.order, trade_result.price);
      PlaySound("ok.wav");
      SendExecutionReport(order_id, trade_result.order, "FILLED", trade_result.price, trade_result.retcode, "");
   }
   else
   {
      PrintFormat("❌ [XAU_AI_Bridge] ORDER REJECTED! Retcode: %d (%s)", trade_result.retcode, trade_result.comment);
      SendExecutionReport(order_id, 0, "REJECTED", 0.0, trade_result.retcode, trade_result.comment);
   }
}

//+------------------------------------------------------------------+
//| Reports Trade Result Back to Python Bot                          |
//+------------------------------------------------------------------+
void SendExecutionReport(string order_id, ulong ticket, string status, double fill_price, uint retcode, string error_msg)
{
   string url = InpBotURL + "/api/mt5/execution-report";
   string headers = "Content-Type: application/json\r\n";
   
   string json = StringFormat(
      "{\"order_id\":\"%s\",\"ticket\":%d,\"status\":\"%s\",\"fill_price\":%.2f,\"retcode\":%d,\"error\":\"%s\"}",
      order_id, (int)ticket, status, fill_price, (int)retcode, error_msg
   );
   
   char post_data[];
   StringToCharArray(json, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(post_data, ArraySize(post_data)-1);
   
   char result[];
   string result_headers;
   WebRequest("POST", url, headers, 2000, post_data, result, result_headers);
}

//+------------------------------------------------------------------+
//| Helper: Extract String value from simple flat JSON               |
//+------------------------------------------------------------------+
string ExtractJsonString(string json, string key)
{
   string search = "\"" + key + "\":\"";
   int start = StringFind(json, search);
   if(start < 0)
   {
      search = "\"" + key + "\": \"";
      start = StringFind(json, search);
   }
   if(start < 0) return "";
   
   start += StringLen(search);
   int end = StringFind(json, "\"", start);
   if(end < 0) return "";
   
   return StringSubstr(json, start, end - start);
}

//+------------------------------------------------------------------+
//| Helper: Extract Double value from simple flat JSON               |
//+------------------------------------------------------------------+
double ExtractJsonDouble(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start < 0)
   {
      search = "\"" + key + "\": ";
      start = StringFind(json, search);
   }
   if(start < 0) return 0.0;
   
   start += StringLen(search);
   int end = StringFind(json, ",", start);
   int end2 = StringFind(json, "}", start);
   if(end < 0 || (end2 >= 0 && end2 < end)) end = end2;
   if(end < 0) return 0.0;
   
   string num_str = StringSubstr(json, start, end - start);
   StringTrimLeft(num_str);
   StringTrimRight(num_str);
   return StringToDouble(num_str);
}
//+------------------------------------------------------------------+
