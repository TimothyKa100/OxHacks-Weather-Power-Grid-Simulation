# ML notes: GB line-corridor outage-risk model

Written from a line-by-line read of `failure_model/` and `backend/` on `hybrid-merge` (commit `8546565`), plus one full retrain on 15 Nov 2026 to check the published numbers. Line references are to this branch.

**One-paragraph summary.** The model is a gradient-boosted tree classifier (XGBoost) that predicts, for each transmission line and each hour, the chance that a weather-caused *distribution* fault is reported within 5 km of the line. It is trained on about 7.4 million line-hours, of which 0.095% are positive. It learns a real but modest signal, and most of that signal is "strong gusts mean more faults". It is not a transmission-line failure model, and the repo says so.

## 1. Data

| | |
|---|---|
| Labels | SSEN Distribution's NaFIRS fault register (SEPD and SHEPD licence areas), April 2015 to October 2026. Read from `data/grid/normalized/all_outage_reports.parquet` (`gb_lines.py:139`). |
| Weather | ERA5 reanalysis via the Open-Meteo archive API, hourly, on a 0.5° grid (roughly 35 × 55 km cells). One parquet per cell in `data/processed/weather/`. |
| Lines | 367-branch GB network at commit `0bb65f8` (`data/gb_network.json`). Only 73 lines are used for training. |
| Size | 7,388,184 rows (73 lines × 101,208 hours), 6,985 positives. |

How the rows are built (`gb_lines.py:239-299`, `build_dataset`):

1. **Filter the fault reports** to overhead-line faults with a weather cause (wind, trees, lightning, ice, snow, rain and so on; the list is at `gb_lines.py:33-34`) and drop anything flagged as possibly planned (`gb_lines.py:143-144`). This leaves 17,881 reports.
2. **Deduplicate**: reports for the same feeder less than 6 hours after the previous one are treated as the same incident (`gb_lines.py:153-160`). This leaves 15,711 incidents.
3. **Draw a 5 km buffer** (a "corridor") around every line and keep only lines whose corridor is at least 90% inside SSEN territory (`gb_lines.py:26-27`, `:249`). Outside SSEN territory there is no continuous fault register, so "no report" would not mean "no fault". This is why only 73 of 367 lines are used.
4. **Label** a line-hour 1 if any incident's first report falls in that hour and inside that corridor (`gb_lines.py:163-170`, `:266-267`).

Split (`gb_model.py:22-50`): strictly by time, no shuffling.

| Part | Rows | Positives | Used for |
|---|---:|---:|---|
| fit (first 60% of hours) | 4,431,027 | 3,996 | growing the trees |
| tune (60–67.5%) | 550,493 | 414 | early stopping and choosing settings |
| calibrate (67.5–75%) | 550,493 | 563 | fixing the probability scale |
| test (last 25%) | 1,845,221 | 2,008 | final scoring |
| purged | 10,950 | 4 | thrown away |

Two details here are good practice. A split boundary that would cut through a storm is moved to the start of that storm (`gb_model.py:31-41`). And rows within 25 hours of a boundary are deleted ("purged", `gb_model.py:48-49`), because a feature such as "rain in the previous 24 hours" would otherwise straddle the boundary and carry information from one side to the other.

Normalisation: none for XGBoost, which is correct. Trees only ask "is this value above or below a threshold", so rescaling a feature changes nothing. The logistic-regression baseline does standardise, with the mean and standard deviation taken from the fit rows only (`gb_model.py:191`).

Cleaning: rows with missing weather make the build fail outright (`gb_lines.py:278-279`); nothing is imputed.

## 2. Features

Twelve inputs. All weather features are computed in `line_features` (`gb_lines.py:191-209`) from per-cell series summarised along the route in `route_weather` (`gb_lines.py:173-188`).

| Feature | How it is computed | Where |
|---|---|---|
| `gust_max_kmh` | Highest gust in any weather cell the route crosses, for the hour being predicted | `gb_lines.py:186`, `:206` |
| `gust_p95_kmh` | 95th percentile of gust along the route, weighted by route length in each cell | `gb_lines.py:186` |
| `gust_prev_3h_max_kmh` | Highest route gust in the three hours before the predicted hour | `gb_lines.py:204` |
| `crosswind_max_kmh` | Gust × \|sin(wind direction − line bearing)\|, maximised over ~2 km route pieces. Wind blowing across a line loads it more than wind along it | `gb_lines.py:179-184` |
| `rain_1h_mm` | Length-weighted rain in the predicted hour | `gb_lines.py:188` |
| `rain_prev_24h_mm` | Rain total over the 24 hours before | `gb_lines.py:205` |
| `temperature_c` | Length-weighted 2 m temperature | `gb_lines.py:188` |
| `length_km`, `ele_mean_m`, `ele_max_m`, `kv`, `circuits` | Fixed properties of the line, read from the network file | `gb_lines.py:37`, `:270-271` |

Model v2 tried four more (`gb_v2.py:43-54`): month as sine and cosine (so December and January are neighbours), highest gust in the previous 6 hours, and accumulated gust above 80 km/h over the previous 6 hours. They did not improve the tuning score and were not adopted for the trees, although the month and gust terms are used in v2's calibrator.

## 3. Target and loss

**Target:** `failed_next_hour` = 1 if at least one new weather-caused SSEN overhead-line fault report lies inside the line's 5 km corridor during the hour `[t, t+1)`.

**Loss:** binary log loss, also called cross-entropy (`objective="binary:logistic"`, `gb_model.py:56`). For one row it is `−log(p)` if the event happened and `−log(1−p)` if it did not. It is the same loss your logistic regression used. It punishes confident wrong answers very heavily, so a model trained on it is pushed towards honest probabilities, not just a correct ranking.

## 4. Model and training

**XGBoost in three sentences.** A decision tree splits the data with yes/no questions on feature values and gives each final group ("leaf") a score. Gradient boosting builds hundreds of small trees one after another, where each new tree is fitted to the errors the trees so far still make. The final prediction is the sum of all the trees' scores, passed through a sigmoid to become a probability.

Settings (`gb_model.py:53-60`):

| Setting | Value | What it does |
|---|---|---|
| `max_depth` | 5 (chosen from 2, 3, 4, 5) | questions per tree path |
| `min_child_weight` | 50 (chosen from 5, 20, 20, 50) | stops leaves built on a handful of rows |
| `learning_rate` | 0.05 | each tree's contribution is shrunk to 5% |
| `n_estimators` | up to 1,500; stopped at 315 | number of trees |
| `early_stopping_rounds` | 50 | stop when the tune loss has not improved for 50 trees |
| `subsample` / `colsample_bytree` | 0.8 / 0.9 | each tree sees a random 80% of rows and 90% of features |
| `reg_lambda` | 5 | L2 penalty on leaf scores |
| `monotone_constraints` | the four wind features | forbids the model from saying more wind is safer (`gb_model.py:26`) |
| `random_state` | 26 | seed |

There is no class weighting or oversampling. That is the right call when the output is used as a probability, because re-weighting the rare class distorts the probabilities.

**Calibration** (`gb_model.py:183`). After training, a one-variable logistic regression is fitted on the calibrate rows, mapping the model's raw score to a probability. This is called Platt scaling. It corrects a model that ranks well but is systematically too high or too low.

**Evaluation** (`failure_model/evaluation.py:8-23`, `gb_model.py:90-109`):

- **Log loss**: as above, averaged over the test rows.
- **Brier score**: mean of (predicted probability − outcome)². Lower is better.
- **Average precision (AP)**: how well the model ranks positives above negatives, in a form that suits rare events. A model guessing at random scores about the event rate, here 0.001.
- **Expected events**: the sum of predicted probabilities. If the model is well calibrated this matches the number of events that happened.
- **Reliability table**: rows grouped by predicted probability, compared with how often the event really occurred in each group.
- **Bootstrap interval**: the Brier score recomputed 1,000 times on resampled storm and month groups, to show how much it would move with different storms.

Baselines trained alongside: a constant (the fit-period event rate) and a four-feature logistic regression on wind, length and voltage (`gb_model.py:190-192`).

**How the model is used downstream.** By default it is only a map layer and a "power-cut forecast" (`backend/forecast.py`). In the optional hybrid mode (`backend/hybrid.py`), each line's ML score is compared with the median score of 5–12 similar lines in the same hour (`backend/ml_risk.py:118-142`), and the difference nudges the engineering failure probability up or down by at most a factor of 2 (`hybrid.py:7`, `:74-98`). The total expected failures per hour is kept the same, so the ML only moves risk between lines.

## 5. Problems

| # | Problem | Where | Confidence |
|---|---|---|---|
| 1 | **The target is a proxy.** The labels are distribution faults near a transmission line, not transmission failures. Fed straight into the simulator as line-failure probability it failed 165 of 367 lines in Storm Darragh, when in reality none did (docs/GB_LINE_MODEL.md). The repo is honest about this. | `gb_lines.py:1-11` | High |
| 2 | **Look-ahead in the weather.** The features for hour `[t, t+1)` use the weather that was later measured for that same hour. A real forecast would only have a prediction of that weather. All reported scores therefore assume a perfect weather forecast. | `gb_lines.py:199` | High |
| 3 | **Model v2's calibrator was chosen on the test set.** The code computes both calibrators' scores on the test rows and keeps the better one. The headline v2 result (storm hours 929 expected against 950 observed) is therefore not a held-out number. The saved `evaluation.json` admits the test period was reused. | `gb_v2.py:146-147` | High |
| 4 | **Positives are counted several times.** Corridors overlap, so one incident is a positive for every line it sits near. I measured this: the 2,008 positive test rows fall in only 898 distinct hours. Rows are far from independent, so the metrics look more certain than they are. | `gb_lines.py:163-170` | High (measured) |
| 5 | **No test on unseen lines.** The same 73 lines appear in fit and test; only the time period differs. Fixed features such as `length_km` are unique per line, so the model can learn "this particular line has many faults" and still score well. 65 of the 73 lines are in northern Scotland. On the NGED area during Darragh the forecast was 3.4 times too low (docs/HYBRID_RESULTS.md). | `gb_model.py:144` | High |
| 6 | **A lookup table gets most of the way there.** I built one: each line's past fault rate multiplied by a factor for five gust bands. On the test set it scores AP 0.060 and log loss 0.00690. XGBoost scores 0.077 and 0.00650. The repo's own baselines are weaker than this, which makes XGBoost look better than it is. | not in repo | High (measured) |
| 7 | **Storm hours are under-predicted by 42%.** 547 expected against 950 observed in my retrain. Storms are the hours the tool exists for. | `evaluation.json` | High |
| 8 | **Not bit-for-bit reproducible.** With the same seed, data and library versions my retrain gave AP 0.0768 and 1,882 expected events; the saved model has 0.0763 and 1,892. The baselines matched exactly, so the difference is inside XGBoost. My guess is multi-threaded training (`n_jobs=-1`), but I did not confirm it. | `gb_model.py:58` | High on the fact, low on the cause |
| 9 | **The training network is no longer in the tree.** The rows were built on the 367-branch network; the checked-in one has 673 branches and reuses the same IDs for different lines. Running `gb-dataset` today silently builds a different dataset. v2 works around it by running `git show 0bb65f8:...` inside training, which fails in a zip download or shallow clone. | `gb_v2.py:40`, `:86` | High |
| 10 | **Missing line properties become zero at inference.** `float(b.get(f) or 0.)` turns a missing elevation or circuit count into 0, which the trees treat as a real value. Training used real values throughout. | `backend/ml_risk.py:163` | Medium |
| 11 | **Label timing is approximate.** The label hour is when the fault was first *reported*, converted from London time by a provisional rule. A fault reported an hour late is matched to the wrong hour's weather. | `gb_lines.py:145`, `:159` | Medium |
| 12 | **The hybrid's knobs are guesses.** β = 0.5, the 0.5–2 multiplier bounds and the 265 km/h pylon fragility median are assumptions with no data behind them. | `backend/hybrid.py:7`, `:143` | High |
| 13 | **Tuning is decided on very few events.** Settings are chosen on a tune period with 414 events, of which 24 are in storm hours. The differences between candidates (0.005026 against 0.005029) are far smaller than the noise. | `gb_model.py:161-177` | Medium |
| 14 | **Cannot be retrained from a fresh clone.** Training needs about 18 GB of data that lives on another branch and on team laptops. | `.gitignore` | High |

## 6. Honest verdict

It works in the narrow sense, and I checked. I rebuilt the dataset from the raw files and retrained v1 from scratch (about 6 minutes on 12 cores). Results on the 1,845,221 test line-hours:

| Model | Log loss | Brier | AP | Expected / observed events |
|---|---:|---:|---:|---:|
| Constant rate | 0.00853 | 0.001087 | 0.001 | 1,664 / 2,008 |
| Each line's own past rate (mine) | 0.00832 | 0.001086 | 0.003 | 1,664 / 2,008 |
| Line rate × gust-band factor (mine) | 0.00690 | 0.001062 | 0.060 | 1,423 / 2,008 |
| Wind-only logistic regression | 0.00702 | 0.001067 | 0.043 | 1,577 / 2,008 |
| **XGBoost, calibrated (my retrain)** | **0.00650** | **0.001041** | **0.077** | 1,882 / 2,008 |
| XGBoost, calibrated (saved in repo) | 0.00650 | 0.001041 | 0.076 | 1,892 / 2,008 |

Reading these:

- The published numbers are real. My retrain matches them to three significant figures.
- The model beats every baseline, so it has learned something.
- Nearly all of the gain comes from gust speed. Faults are 52 times more likely than average when the route gust is above 100 km/h and about half as likely below 40 km/h. A five-row table captures most of that.
- The Brier score improves by only 4% over predicting a constant. With events this rare, almost every prediction is "nearly zero" and almost every outcome is zero, so the Brier score barely moves. AP and log loss are the more informative numbers here.
- It does not answer the question the project asks (which transmission lines fail). The team's choice to keep it as a separate layer by default is the correct reading of the evidence.

I did not retrain v2; its saved benchmark is contaminated by problem 3 in any case.

## 7. Improvements, ranked by impact against effort

**Quick fixes (hours)**

1. Add the "line rate × gust band" table as an official baseline. It changes the story from "70 times better than chance" to "about 30% better than a lookup table", which is the honest claim.
2. Choose v2's calibrator on the calibrate period, not the test period, and re-report.
3. Commit the 367-branch network as `data/gb_network_training.json` and remove the `git show` call.
4. Set `n_jobs=1` (or XGBoost's deterministic histogram option) for the final fit and confirm two runs give identical files.
5. Pass NaN, not 0, for missing line properties at inference.

**Proper experimental setup (days)**

6. **Hold out lines, not just time.** Train on some lines and test on others (grouped cross-validation by line or by region). This measures whether the model transfers to lines it has never seen, which is what the map does for 294 of 367 lines.
7. **Count each incident once.** Either assign an incident to its nearest line only, or evaluate per incident-hour.
8. **Ablations.** Retrain with groups of features removed: no line properties, gust only, no rain and temperature. This shows what each group contributes.
9. **Score with forecasts.** Replace reanalysis with archived weather forecasts for the test period to see the performance a user would get.
10. Report storm-hour metrics as the headline, since quiet hours dominate the totals.

**Bigger changes (weeks)**

11. Predict the *number* of faults per corridor-hour (a Poisson model) so that five faults count for more than one. Storm under-prediction is partly a consequence of collapsing counts to 0/1.
12. Add a storm-level effect so that lines in the same storm are not treated as independent.
13. Use finer weather (the 2 km UKV model) where it exists; a 0.5° cell averages away the local gust peaks that break lines.
14. Get real transmission outage records. No amount of modelling fixes problem 1.

## 8. Concepts to learn

- **Gradient-boosted trees.** Many small decision trees added together, each one correcting the last. On tables of mixed numeric features they usually beat neural networks and need little preprocessing. Worth learning next after logistic regression.
- **Class imbalance.** When one outcome is very rare (here 1 in 1,000), accuracy is useless: predicting "never" is 99.9% accurate. You need metrics that focus on the rare class, such as average precision, and you should be suspicious of any single headline number.
- **Calibration.** A model is calibrated if, of all the times it says 10%, the event happens about 10% of the time. Ranking well and being calibrated are different properties, and a simulator that draws random failures needs the second.
- **Proper scoring rules (log loss, Brier score).** Loss functions that are minimised only by reporting your true belief. They reward honest probabilities, where accuracy only rewards being on the right side of 50%.
- **Temporal splits and purging.** With time-ordered data you must train on the past and test on the future, and leave a gap as wide as your longest look-back feature. A random split lets the model see the same storm on both sides.
- **Data leakage and look-ahead bias.** Any path by which information unavailable at prediction time reaches the model. Problems 2 and 3 above are two different kinds: one in the features, one in model selection.
- **Proxy targets and distribution shift.** Training on a stand-in label (distribution faults) or a stand-in population (northern Scotland) and applying the model elsewhere. The model can be excellent on its own test set and wrong where it is used.
- **Grouped (non-independent) data.** Rows from the same line or the same storm resemble each other, so 1.8 million rows is nowhere near 1.8 million independent tests. Splits and confidence intervals should be built from whole groups.
- **Baselines and ablations.** A model's score means nothing alone. You compare it with the simplest thing that could work, and you remove parts to see which ones matter.
- **Monotonic constraints.** Telling the model a relationship can only go one way (more wind never lowers risk). It is a cheap way to add physical knowledge and it stops the model learning noise in sparse regions.

## 9. Changes made on the `cleanup` branch (15 Nov 2026)

The saved models in `demo_data/` are untouched. These changes affect code and any future retrain.

| Change | Problem fixed | Effect on results |
|---|---|---|
| v2's calibrator is now chosen on the tuning period (`gb_v2.py`, `calibrator_selection` in the manifest) | 3 | **None on the choice.** I retrained v2 with the new rule: it still picks the seasonal calibrator (tune storm hours 25.1 expected against 24 observed, versus 21.6 for the plain sigmoid). The test period no longer takes part in any decision. |
| The 367-branch training network is committed as `failure_model/assets/gb_network_training_0bb65f8.json`; `gb-dataset` builds on it and v2 no longer shells out to git | 9 | None. This is the network the saved rows were built on. |
| Missing line properties stay NaN at inference (`backend/ml_risk.py`) | 10 | None today: no overhead branch in the current network is missing one. It was a latent bug. |
| New baseline `line_rate_gust_band_baseline` in v1's evaluation report (`gb_model.rate_gust_baseline`), with a unit test | 6 | Adds a row to future reports. Measured value is in section 6. |

v2 retrain on this machine, old test period (a benchmark, not a clean test, because v1's results on it shaped v2):

| | Log loss | AP | Storm expected / observed | Quiet expected / observed |
|---|---:|---:|---:|---:|
| Saved in repo, seasonal calibrator | 0.006473 | 0.0781 | 929 / 950 | 1,394 / 1,058 |
| My retrain, seasonal calibrator | 0.006477 | 0.0764 | 918 / 950 | 1,377 / 1,058 |
| Saved in repo, plain sigmoid | 0.006501 | 0.0768 | 550 / 950 | 1,341 / 1,058 |
| My retrain, plain sigmoid | 0.006506 | 0.0749 | 799 / 950 | 1,367 / 1,058 |

One thing this exposed: the same code and seed stopped at 174 trees here and 255 in the saved model, and under the plain sigmoid the expected storm count moved from 550 to 799. The tuning loss is almost flat across tree counts (0.005026 against 0.005027), so early stopping lands somewhere different on a different machine. The seasonal calibrator is much more stable (929 against 918). I have not found the cause of the run-to-run difference; treat storm-hour totals from this model as uncertain by a wide margin.
