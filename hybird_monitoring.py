#!/usr/bin/env python3
"""
Trade Monitor Dashboard for Hybrid Trading System
Displays real-time statistics and trade history
"""

import json
import os
import time
from datetime import datetime as dt, timedelta
from typing import List, Dict

class TradeMonitor:
    def __init__(self):
        self.trades_file = "paper_trades.json"
        self.config_file = "config_summary.json"
        
    def load_trades(self) -> List[Dict]:
        """Load trades from JSON file"""
        if os.path.exists(self.trades_file):
            try:
                with open(self.trades_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading trades: {e}")
        return []
    
    def calculate_stats(self, trades: List[Dict]) -> Dict:
        """Calculate trading statistics"""
        if not trades:
            return {
                "total_trades": 0,
                "buy_orders": 0,
                "sell_orders": 0,
                "total_volume": 0,
                "avg_price": 0,
                "price_range": {"min": 0, "max": 0},
                "last_trade": None,
                "trading_session": {"start": None, "duration": "0:00:00"}
            }
        
        buy_orders = [t for t in trades if t["action"] == "BUY"]
        sell_orders = [t for t in trades if t["action"] == "SELL"]
        
        prices = [float(t["price"]) for t in trades]
        volumes = [int(t["quantity"]) for t in trades]
        
        # Parse timestamps
        timestamps = []
        for trade in trades:
            try:
                if isinstance(trade["timestamp"], str):
                    ts = dt.fromisoformat(trade["timestamp"].replace("Z", "+00:00"))
                else:
                    ts = dt.now()  # Fallback
                timestamps.append(ts)
            except:
                timestamps.append(dt.now())
        
        session_start = min(timestamps) if timestamps else dt.now()
        session_end = max(timestamps) if timestamps else dt.now()
        duration = session_end - session_start
        
        return {
            "total_trades": len(trades),
            "buy_orders": len(buy_orders),
            "sell_orders": len(sell_orders),
            "total_volume": sum(volumes),
            "avg_price": sum(prices) / len(prices) if prices else 0,
            "price_range": {"min": min(prices) if prices else 0, "max": max(prices) if prices else 0},
            "last_trade": trades[-1] if trades else None,
            "trading_session": {
                "start": session_start.strftime("%H:%M:%S"),
                "duration": str(duration).split(".")[0]  # Remove microseconds
            }
        }
    
    def calculate_pnl(self, trades: List[Dict]) -> Dict:
        """Calculate P&L from matched buy/sell pairs"""
        positions = {}
        completed_trades = []
        total_pnl = 0
        
        for trade in trades:
            instrument = trade["instrument_key"]
            
            if instrument not in positions:
                positions[instrument] = {"quantity": 0, "avg_price": 0, "total_cost": 0}
            
            pos = positions[instrument]
            
            if trade["action"] == "BUY":
                # Add to position
                new_qty = pos["quantity"] + trade["quantity"]
                new_cost = pos["total_cost"] + (trade["price"] * trade["quantity"])
                pos["quantity"] = new_qty
                pos["total_cost"] = new_cost
                pos["avg_price"] = new_cost / new_qty if new_qty > 0 else 0
                
            elif trade["action"] == "SELL" and pos["quantity"] > 0:
                # Close position
                sell_qty = min(trade["quantity"], pos["quantity"])
                sell_value = trade["price"] * sell_qty
                cost_basis = pos["avg_price"] * sell_qty
                
                trade_pnl = sell_value - cost_basis
                total_pnl += trade_pnl
                
                completed_trades.append({
                    "instrument": instrument,
                    "entry_price": pos["avg_price"],
                    "exit_price": trade["price"],
                    "quantity": sell_qty,
                    "pnl": trade_pnl,
                    "exit_time": trade["timestamp"]
                })
                
                # Update position
                pos["quantity"] -= sell_qty
                if pos["quantity"] <= 0:
                    pos["quantity"] = 0
                    pos["avg_price"] = 0
                    pos["total_cost"] = 0
                else:
                    pos["total_cost"] = pos["avg_price"] * pos["quantity"]
        
        return {
            "total_pnl": total_pnl,
            "completed_trades": len(completed_trades),
            "open_positions": sum(1 for pos in positions.values() if pos["quantity"] > 0),
            "best_trade": max(completed_trades, key=lambda x: x["pnl"], default=None) if completed_trades else None,
            "worst_trade": min(completed_trades, key=lambda x: x["pnl"], default=None) if completed_trades else None,
            "win_rate": sum(1 for t in completed_trades if t["pnl"] > 0) / len(completed_trades) * 100 if completed_trades else 0
        }
    
    def display_dashboard(self):
        """Display the main dashboard"""
        os.system('clear' if os.name == 'posix' else 'cls')  # Clear screen
        
        print("📊 HYBRID TRADING SYSTEM - LIVE DASHBOARD")
        print("=" * 60)
        print(f"🕒 Last Updated: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"💼 Mode: LIVE Data + PAPER Trading")
        print("=" * 60)
        
        # Load and analyze trades
        trades = self.load_trades()
        stats = self.calculate_stats(trades)
        pnl = self.calculate_pnl(trades)
        
        # System Status
        print("\n🔥 SYSTEM STATUS")
        print("-" * 30)
        print(f"📈 Session Start: {stats['trading_session']['start']}")
        print(f"⏱️  Session Duration: {stats['trading_session']['duration']}")
        print(f"🔄 Total Orders: {stats['total_trades']}")
        print(f"📊 Data Source: Live Market Feed")
        
        # Trade Statistics
        print(f"\n📋 TRADE STATISTICS")
        print("-" * 30)
        print(f"🟢 Buy Orders: {stats['buy_orders']}")
        print(f"🔴 Sell Orders: {stats['sell_orders']}")
        print(f"📦 Total Volume: {stats['total_volume']:,}")
        print(f"💰 Average Price: ₹{stats['avg_price']:.2f}")
        print(f"📏 Price Range: ₹{stats['price_range']['min']:.2f} - ₹{stats['price_range']['max']:.2f}")
        
        # P&L Analysis
        print(f"\n💹 P&L ANALYSIS")
        print("-" * 30)
        print(f"💰 Total P&L: ₹{pnl['total_pnl']:.2f}")
        print(f"✅ Completed Trades: {pnl['completed_trades']}")
        print(f"📊 Open Positions: {pnl['open_positions']}")
        print(f"🎯 Win Rate: {pnl['win_rate']:.1f}%")
        
        if pnl['best_trade']:
            print(f"🏆 Best Trade: ₹{pnl['best_trade']['pnl']:.2f}")
        if pnl['worst_trade']:
            print(f"📉 Worst Trade: ₹{pnl['worst_trade']['pnl']:.2f}")
        
        # Last Trade Info
        if stats['last_trade']:
            last = stats['last_trade']
            print(f"\n🔄 LAST TRADE")
            print("-" * 30)
            print(f"Action: {last['action']}")
            print(f"Price: ₹{last['price']}")
            print(f"Quantity: {last['quantity']}")
            print(f"Instrument: {last['instrument_key'].split('|')[-1]}")
        
        # Recent Trades
        print(f"\n📝 RECENT TRADES (Last 5)")
        print("-" * 30)
        recent_trades = trades[-5:] if len(trades) >= 5 else trades
        
        for i, trade in enumerate(reversed(recent_trades)):
            action_emoji = "🟢" if trade['action'] == 'BUY' else "🔴"
            timestamp = trade['timestamp']
            if isinstance(timestamp, str):
                try:
                    ts = dt.fromisoformat(timestamp.replace("Z", "+00:00"))
                    time_str = ts.strftime("%H:%M:%S")
                except:
                    time_str = "Unknown"
            else:
                time_str = "Unknown"
            
            print(f"{action_emoji} {time_str} | {trade['action']} {trade['quantity']} @ ₹{trade['price']}")
        
        if not trades:
            print("No trades executed yet...")
        
        print("\n" + "=" * 60)
        print("Press Ctrl+C to stop monitoring | Updates every 5 seconds")
        print("💡 Tip: Keep this running while your trading script is active")
    
    def run_monitor(self):
        """Run the monitoring loop"""
        try:
            while True:
                self.display_dashboard()
                time.sleep(5)  # Update every 5 seconds
        except KeyboardInterrupt:
            print("\n\n👋 Monitor stopped. Happy trading!")
        except Exception as e:
            print(f"\n❌ Monitor error: {e}")

def show_trade_summary():
    """Show a one-time trade summary"""
    monitor = TradeMonitor()
    trades = monitor.load_trades()
    
    if not trades:
        print("📭 No trades found in paper_trades.json")
        return
    
    stats = monitor.calculate_stats(trades)
    pnl = monitor.calculate_pnl(trades)
    
    print("📊 TRADE SESSION SUMMARY")
    print("=" * 40)
    print(f"Total Orders: {stats['total_trades']}")
    print(f"Total Volume: {stats['total_volume']:,}")
    print(f"Average Price: ₹{stats['avg_price']:.2f}")
    print(f"Total P&L: ₹{pnl['total_pnl']:.2f}")
    print(f"Win Rate: {pnl['win_rate']:.1f}%")
    print(f"Completed Trades: {pnl['completed_trades']}")
    print("=" * 40)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        show_trade_summary()
    else:
        monitor = TradeMonitor()
        print("🚀 Starting Trade Monitor Dashboard...")
        print("💡 Run 'python3 trade_monitor.py summary' for a quick summary")
        time.sleep(2)
        monitor.run_monitor()