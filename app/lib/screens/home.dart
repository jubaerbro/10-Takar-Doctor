import 'dart:async';

import 'package:flutter/material.dart';

import '../state.dart';
import '../strings.dart';
import 'call.dart';
import 'onboarding.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Timer? _watcher;
  Map<String, dynamic>? _waiting; // our parked request, if a doctor of the wanted type was not online
  bool _callOpen = false;
  bool _checking = false;
  bool _starting = false; // a new search is being sent; the watcher must not open it first

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((Duration _) => _checkCurrent());
    _watcher = Timer.periodic(const Duration(seconds: 4), (Timer timer) => _checkCurrent());
  }

  @override
  void dispose() {
    _watcher?.cancel();
    super.dispose();
  }

  /// Ask the server whether we have anything live. A search or call that is under way is reopened
  /// (for example after the app was closed). A waiting request is shown as a banner until a doctor
  /// picks it up, and then the call screen opens by itself.
  Future<void> _checkCurrent() async {
    if (_checking || _callOpen || _starting || !mounted) return;
    _checking = true;
    try {
      final Map<String, dynamic> result = await appState.api.get('/consultations/current');
      final dynamic current = result['consultation'];
      if (!mounted || _callOpen) return;
      if (current is! Map) {
        if (_waiting != null) setState(() => _waiting = null);
      } else if (current['status'] == 'waiting') {
        setState(() => _waiting = current.cast<String, dynamic>());
      } else {
        setState(() => _waiting = null);
        _openCall(current.cast<String, dynamic>(), null);
      }
    } catch (_) {
      // Not being able to check is not worth interrupting the user for; we try again in a few seconds.
    } finally {
      _checking = false;
    }
  }

  Future<void> _openCall(Map<String, dynamic> consultation, Map<String, dynamic>? request) async {
    if (_callOpen) return;
    _callOpen = true;
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (BuildContext context) => CallScreen(consultation: consultation, request: request),
    ));
    _callOpen = false;
    try {
      await appState.refreshMe();
    } catch (_) {}
    _checkCurrent();
  }

  Future<void> _findDoctor() async {
    if (appState.balance < appState.callPrice) {
      showMessage(context, t('low_balance'));
      await _open(const WalletScreen());
      return;
    }
    final Map<String, dynamic>? request = await Navigator.of(context).push(MaterialPageRoute<Map<String, dynamic>>(
      builder: (BuildContext context) => const FindDoctorScreen(),
    ));
    if (request == null || !mounted) return;
    _starting = true;
    Map<String, dynamic>? consultation;
    try {
      consultation = await appState.api.post('/match', request);
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    _starting = false;
    if (consultation != null && mounted) await _openCall(consultation, request);
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (BuildContext context) => screen));
    try {
      await appState.refreshMe();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: appState,
      builder: (BuildContext context, Widget? child) {
        final String priceLine = t('price_line')
            .replaceAll('{price}', taka(appState.callPrice))
            .replaceAll('{minutes}', appState.callMinutes.toString());
        return Scaffold(
          appBar: AppBar(
            title: Text(t('app_name')),
            actions: <Widget>[
              const LanguageButton(),
              IconButton(tooltip: t('logout'), onPressed: appState.logout, icon: const Icon(Icons.logout)),
            ],
          ),
          body: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Container(
                    padding: const EdgeInsets.all(20),
                    decoration: BoxDecoration(color: colors.primaryContainer, borderRadius: BorderRadius.circular(16)),
                    child: Row(
                      children: <Widget>[
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(t('balance')),
                              Text(taka(appState.balance), style: const TextStyle(fontSize: 32, fontWeight: FontWeight.bold)),
                            ],
                          ),
                        ),
                        OutlinedButton.icon(
                          onPressed: () => _open(const WalletScreen()),
                          icon: const Icon(Icons.add),
                          label: Text(t('top_up')),
                        ),
                      ],
                    ),
                  ),
                  if (_waiting != null)
                    Container(
                      margin: const EdgeInsets.only(top: 12),
                      padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
                      decoration: BoxDecoration(color: Colors.amber.shade100, borderRadius: BorderRadius.circular(12)),
                      child: Row(
                        children: <Widget>[
                          const Icon(Icons.hourglass_top),
                          const SizedBox(width: 12),
                          Expanded(child: Text(t('waiting_banner'))),
                          TextButton(
                            onPressed: () {
                              final Map<String, dynamic>? waiting = _waiting;
                              if (waiting != null) _openCall(waiting, null);
                            },
                            child: Text(t('view')),
                          ),
                        ],
                      ),
                    ),
                  const Spacer(),
                  Icon(Icons.video_call, size: 96, color: colors.primary),
                  const SizedBox(height: 12),
                  Text(priceLine, textAlign: TextAlign.center, style: const TextStyle(fontSize: 17)),
                  const SizedBox(height: 16),
                  FilledButton(
                    style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(72)),
                    onPressed: _findDoctor,
                    child: Text(t('find_doctor'), style: const TextStyle(fontSize: 22)),
                  ),
                  const Spacer(),
                  TextButton.icon(
                    onPressed: () => _open(const HistoryScreen()),
                    icon: const Icon(Icons.history),
                    label: Text(t('history')),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

// ----------------------------------------------------------------------------- choose filters

/// Lets the patient pick filters and describe the problem. Pops with the body for POST /match.
class FindDoctorScreen extends StatefulWidget {
  const FindDoctorScreen({super.key});

  @override
  State<FindDoctorScreen> createState() => _FindDoctorScreenState();
}

class _FindDoctorScreenState extends State<FindDoctorScreen> {
  final TextEditingController _problem = TextEditingController();
  String? _specialty;
  String? _language;
  String? _gender;

  @override
  void dispose() {
    _problem.dispose();
    super.dispose();
  }

  Widget _choices(String title, Map<String?, String> options, String? selected, ValueChanged<String?> onPick) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: <Widget>[
            for (final MapEntry<String?, String> option in options.entries)
              ChoiceChip(
                label: Text(option.value),
                selected: selected == option.key,
                onSelected: (bool on) => onPick(option.key),
              ),
          ],
        ),
        const SizedBox(height: 20),
      ],
    );
  }

  Future<void> _submit() async {
    final String problem = _problem.text.trim();
    if (problem.length < 2) {
      showMessage(context, t('fill_all'));
      return;
    }
    // Emergencies must not wait in a queue: ask before every search.
    final bool? isEmergency = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (BuildContext context) => AlertDialog(
        title: Text(t('emergency_title')),
        content: SingleChildScrollView(child: Text(t('emergency_body'))),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.of(context).pop(true), child: Text(t('emergency_yes'))),
          FilledButton(
            style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(t('emergency_no')),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (isEmergency != false) {
      showMessage(context, t('emergency_call'));
      return;
    }
    Navigator.of(context).pop(<String, dynamic>{
      'problem': problem,
      if (_specialty != null) 'specialty': _specialty,
      if (_language != null) 'language': _language,
      if (_gender != null) 'doctor_gender': _gender,
    });
  }

  @override
  Widget build(BuildContext context) {
    final String lang = appState.lang;
    return Scaffold(
      appBar: AppBar(title: Text(t('find_doctor'))),
      body: SafeArea(
        child: Column(
          children: <Widget>[
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(24),
                children: <Widget>[
                  TextField(
                    controller: _problem,
                    maxLength: 300,
                    maxLines: 2,
                    decoration: InputDecoration(labelText: t('problem'), hintText: t('problem_hint')),
                  ),
                  const SizedBox(height: 12),
                  _choices(
                    t('doctor_type'),
                    <String?, String>{
                      null: t('any'),
                      for (final String s in appState.specialties) s: specialtyName(lang, s),
                    },
                    _specialty,
                    (String? v) => setState(() => _specialty = v),
                  ),
                  _choices(
                    t('doctor_language'),
                    <String?, String>{null: t('any'), 'bn': 'বাংলা', 'en': 'English'},
                    _language,
                    (String? v) => setState(() => _language = v),
                  ),
                  _choices(
                    t('doctor_gender'),
                    <String?, String>{null: t('any'), 'female': t('female'), 'male': t('male')},
                    _gender,
                    (String? v) => setState(() => _gender = v),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 0, 24, 16),
              child: FilledButton(
                onPressed: _submit,
                child: Text(t('start_search').replaceAll('{price}', taka(appState.callPrice))),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- wallet

class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key});

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  Map<String, dynamic>? _wallet;
  int _amount = 50;
  String _method = 'bkash';
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final Map<String, dynamic> wallet = await appState.api.get('/wallet');
      if (mounted) setState(() => _wallet = wallet);
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
  }

  Future<void> _topUp() async {
    setState(() => _busy = true);
    try {
      final Map<String, dynamic> topup = await appState.api
          .post('/wallet/topups', <String, dynamic>{'amount_taka': _amount, 'method': _method});
      // Until bKash/Nagad are connected there is no payment page to open. A test server lets us
      // confirm the top-up directly; a live server refuses, and we tell the user plainly.
      try {
        await appState.api.post('/dev/topups/${topup['id']}/confirm');
        if (mounted) showMessage(context, t('topup_done'));
      } catch (_) {
        if (mounted) showMessage(context, t('topup_unavailable'));
      }
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final Map<String, dynamic>? wallet = _wallet;
    final List<dynamic> transactions = (wallet?['transactions'] ?? <dynamic>[]) as List<dynamic>;
    return Scaffold(
      appBar: AppBar(title: Text(t('wallet'))),
      body: wallet == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(24),
              children: <Widget>[
                Text(t('balance')),
                Text(taka(wallet['balance_taka'] as num), style: const TextStyle(fontSize: 36, fontWeight: FontWeight.bold)),
                const SizedBox(height: 24),
                Text(t('amount'), style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  children: <Widget>[
                    for (final int amount in <int>[20, 50, 100, 200, 500])
                      ChoiceChip(
                        label: Text(taka(amount)),
                        selected: _amount == amount,
                        onSelected: (bool on) => setState(() => _amount = amount),
                      ),
                  ],
                ),
                const SizedBox(height: 16),
                Text(t('pay_with'), style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  children: <Widget>[
                    for (final MapEntry<String, String> m
                        in <String, String>{'bkash': 'bKash', 'nagad': 'Nagad', 'card': 'Card'}.entries)
                      ChoiceChip(
                        label: Text(m.value),
                        selected: _method == m.key,
                        onSelected: (bool on) => setState(() => _method = m.key),
                      ),
                  ],
                ),
                const SizedBox(height: 16),
                FilledButton(onPressed: _busy ? null : _topUp, child: Text('${t('top_up')} · ${taka(_amount)}')),
                const SizedBox(height: 24),
                for (final dynamic tx in transactions)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(_transactionName(tx['type'] as String)),
                    subtitle: Text(_dateText(tx['created_at'] as num)),
                    trailing: Text(
                      taka(tx['amount_taka'] as num),
                      style: TextStyle(
                        fontSize: 16,
                        color: (tx['amount_taka'] as num) < 0 ? Colors.red.shade700 : Colors.green.shade700,
                      ),
                    ),
                  ),
              ],
            ),
    );
  }
}

String _transactionName(String type) {
  final String key = 'tx_$type';
  final String name = t(key);
  return name == key ? type : name;
}

String _dateText(num unixSeconds) {
  final DateTime d = DateTime.fromMillisecondsSinceEpoch((unixSeconds * 1000).round());
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(d.day)}/${two(d.month)}/${d.year} ${two(d.hour)}:${two(d.minute)}';
}

// ----------------------------------------------------------------------------- history

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  List<dynamic>? _items;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final Map<String, dynamic> result = await appState.api.get('/consultations');
      if (mounted) setState(() => _items = result['consultations'] as List<dynamic>);
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final List<dynamic>? items = _items;
    Widget body;
    if (items == null) {
      body = const Center(child: CircularProgressIndicator());
    } else if (items.isEmpty) {
      body = Center(child: Text(t('no_history')));
    } else {
      body = ListView.separated(
        itemCount: items.length,
        separatorBuilder: (BuildContext context, int index) => const Divider(height: 1),
        itemBuilder: (BuildContext context, int index) {
          final Map<String, dynamic> c = (items[index] as Map).cast<String, dynamic>();
          final dynamic doctor = c['doctor'];
          return ListTile(
            leading: const Icon(Icons.person),
            title: Text(doctor is Map ? doctor['name'] as String : t('call_ended')),
            subtitle: Text('${_dateText(c['created_at'] as num)} · ${c['problem']}'),
            trailing: Text(taka(c['price_charged_taka'] as num)),
            onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
              builder: (BuildContext context) => CallScreen(consultation: c, request: null),
            )),
          );
        },
      );
    }
    return Scaffold(appBar: AppBar(title: Text(t('history'))), body: body);
  }
}
