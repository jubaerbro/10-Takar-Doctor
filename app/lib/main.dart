import 'package:flutter/material.dart';

import 'screens/home.dart';
import 'screens/onboarding.dart';
import 'state.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const TenTakaApp());
  appState.start();
}

const Color brandGreen = Color(0xFF1B8A5A);

class TenTakaApp extends StatelessWidget {
  const TenTakaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: appState,
      builder: (BuildContext context, Widget? child) {
        return MaterialApp(
          title: '10 Takar Doctor',
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
            useMaterial3: true,
            colorScheme: ColorScheme.fromSeed(seedColor: brandGreen),
            scaffoldBackgroundColor: Colors.white,
            filledButtonTheme: FilledButtonThemeData(
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(56),
                textStyle: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
              ),
            ),
            inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder()),
          ),
          // Keyed by language so that switching language redraws the whole screen.
          home: KeyedSubtree(key: ValueKey<String>(appState.lang), child: _currentScreen()),
        );
      },
    );
  }

  /// Which screen the user should be on, decided from what the server knows about them.
  Widget _currentScreen() {
    if (appState.loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    final String? error = appState.startError;
    if (error != null) {
      return StartErrorScreen(message: error);
    }
    final Map<String, dynamic>? me = appState.me;
    if (me == null) {
      return const LoginScreen();
    }
    if (me['consent_needed'] == true) {
      return const ConsentScreen();
    }
    if (me['profile'] == null) {
      return const ProfileScreen();
    }
    return const HomeScreen();
  }
}
