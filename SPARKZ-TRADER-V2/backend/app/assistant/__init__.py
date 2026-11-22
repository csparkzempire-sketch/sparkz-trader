"""
Market assistant: reads the market on several timeframes with several trading strategies before the robot
may trade, and says which strategies fit the market now, which way they point, how strongly they agree, a
trade plan, and how long similar signals took to play out in the past.

It describes the market; it cannot know the future. Strategy-fit percentages measure how well each strategy's
conditions match the current candles; time ranges and hit rates come from the same detector's past signals.
"""
