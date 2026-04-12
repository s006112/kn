<?php
session_start();
// Buffer output so headers (redirects) can be sent from form blocks
ob_start();
if (isset($_POST["webPageInput"])) {
    $text = $_POST["webPageInput"];
    $lines = preg_split('/\r?\n/', $text, 2);
    $title = isset($lines[0]) ? trim($lines[0]) : '';

    $filename = preg_replace('/[\\/:*?"<>|\r\n]+/', '', $title);
    $filename = mb_substr($filename, 0, 50);
    $filename = trim($filename);
    if ($filename === '') $filename = 'untitled';
    $filename .= '.txt';

    $text = preg_replace('/\bImage\b/i', '', $text);
    $text = preg_replace('/\b\d{2}\b/', '', $text);
    $text = preg_replace('/https?:\/\/\S+|www\.\S+/i', '', $text);

    if (preg_match('/[\x{4e00}-\x{9fa5}]/u', $text)) {
        $text = preg_replace('/\s+/u', '', $text);
    } else {
        $text = preg_replace('/\s+/u', ' ', trim($text));
    }
    $text = preg_replace('/\n{2,}/', "\n", $text);

    header('Content-Type: text/plain; charset=UTF-8');
    header('Content-Disposition: attachment; filename="' . $filename . '"');
    header('Content-Length: ' . strlen($text));
    echo $text;
    exit;
}
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tools</title>
    <link rel="icon" type="image/png" href="Tools.png">
    <style>
        body {
            margin: 20px;
            font-family: "Microsoft YaHei", "SimSun", Arial, sans-serif;
            background-color: #121212;
            color: #f5f5f5;
        }
        .grid-container {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            max-width: 1200px;
            margin: 0 auto;
        }
        .form-container {
            background-color: #333;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        textarea {
            width: 100%;
            height: 60px;
            font-family: inherit;
            padding: 8px;
            box-sizing: border-box;
            margin-bottom: 10px;
            border: 1px solid #555;
            border-radius: 4px;
            background-color: #444;
            color: #f5f5f5;
        }
        /* Let the web page cleaner textarea honor its rows attribute */
        #webPageInput { height: auto; }
        button {
            width: 100%;
            padding: 8px;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        button:hover {
            background-color: #45a049;
        }
    </style>
</head>
<body>
    <div class="grid-container">
        <!-- First Form: Chinese Conversion -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="userInput" name="userInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST["userInput"])) {
                        $text = $_POST["userInput"];
                        
                        function toSimplified($text) {
                            $from = 'zh-TW';
                            $to = 'zh-CN';
                            
                            $text = urlencode($text);
                            $url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl={$from}&tl={$to}&dt=t&q={$text}";
                            
                            $ch = curl_init();
                            curl_setopt($ch, CURLOPT_URL, $url);
                            curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
                            curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
                            curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0');
                            
                            $response = curl_exec($ch);
                            curl_close($ch);
                            
                            if ($response === false) {
                                return $text;
                            }
                            
                            $result = json_decode($response, true);
                            if (!$result) {
                                return $text;
                            }
                            
                            $translated_text = '';
                            foreach ($result[0] as $segment) {
                                if (isset($segment[0])) {
                                    $translated_text .= $segment[0];
                                }
                            }
                            
                            return $translated_text ?: $text;
                        }
                        
                        $converted = toSimplified($text);
                        $_SESSION['results']['userInput'] = $converted;
                        header('Location: ' . $_SERVER['PHP_SELF'] . '#userInput');
                        exit;
                    }
                    if (isset($_SESSION['results']['userInput'])) {
                        echo htmlspecialchars($_SESSION['results']['userInput'], ENT_QUOTES, 'UTF-8');
                        unset($_SESSION['results']['userInput']);
                    }
                ?></textarea>
                <button type="submit">Traditional to Simplified</button>
            </form>
        </div>

        <!-- Fifth Form: Add dotted list -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="addDottedListInput" name="addDottedListInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST["addDottedListInput"])) {
                        $text = $_POST["addDottedListInput"];
                        
                        $lines = explode("\n", $text);
                        $result = array_map(function($line) {
                            $originalIndent = '';
                            if (preg_match('/^(\s+)/', $line, $matches)) {
                                $originalIndent = $matches[1];
                            }
                            if (strpos($line, '- ') !== false) {
                                return $line;
                            }
                            if (trim($line) !== '') {
                                return $originalIndent . "- " . ltrim($line);
                            }
                            return $line;
                        }, $lines);
                        $result = array_filter($result, function($line) {
                            return trim($line) !== '';
                        });
                        $result = implode("\n", $result);
                        
                        $_SESSION['results']['addDottedListInput'] = $result;
                        header('Location: ' . $_SERVER['PHP_SELF'] . '#addDottedListInput');
                        exit;
                    }
                    if (isset($_SESSION['results']['addDottedListInput'])) {
                        echo htmlspecialchars($_SESSION['results']['addDottedListInput'], ENT_QUOTES, 'UTF-8');
                        unset($_SESSION['results']['addDottedListInput']);
                    }
                ?></textarea>
                <button type="submit">Add dotted list</button>
            </form>
        </div>

        <!-- Third Form: Remove All Whitespace -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="whitespaceInput" name="whitespaceInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST["whitespaceInput"])) {
                        $text = $_POST["whitespaceInput"];
                        $result = preg_replace('/\s+/', '', $text);
                        $_SESSION['results']['whitespaceInput'] = $result;
                        header('Location: ' . $_SERVER['PHP_SELF'] . '#whitespaceInput');
                        exit;
                    }
                    if (isset($_SESSION['results']['whitespaceInput'])) {
                        echo htmlspecialchars($_SESSION['results']['whitespaceInput'], ENT_QUOTES, 'UTF-8');
                        unset($_SESSION['results']['whitespaceInput']);
                    }
                ?></textarea>
                <button type="submit">Remove All Whitespace</button>
            </form>
        </div>

               
        <!-- Second Form: Remove Empty Lines -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="lineInput" name="lineInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["lineInput"])) {
                        $text = $_POST["lineInput"];
                        
                        // Split text into lines, keep markdown separator lines as blanks, remove other empty ones, and rejoin
                        $lines = explode("\n", $text);
                        $blankLinePlaceholder = '__KEEP_BLANK_LINE__';
                        $lines = array_map(function($line) use ($blankLinePlaceholder) {
                            $trimmedLine = trim($line);
                            if ($trimmedLine === '---') {
                                return $blankLinePlaceholder;
                            }
                            return $line;
                        }, $lines);
                        $lines = array_filter($lines, function($line) {
                            return trim($line) !== '';
                        });
                        $lines = array_map(function($line) use ($blankLinePlaceholder) {
                            return $line === $blankLinePlaceholder ? '' : $line;
                        }, $lines);
                        $result = implode("\n", $lines);
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove Empty Lines</button>
            </form>
        </div>

        <!-- Remove Empty Lines, Reset ### -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="lineInput" name="lineInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["lineInput"])) {
                        $text = $_POST["lineInput"];
                        
                        // Split text into lines, keep markdown separator lines as blanks, remove other empty ones, and rejoin
                        $lines = explode("\n", $text);
                        $blankLinePlaceholder = '__KEEP_BLANK_LINE__';
                        $lines = array_map(function($line) use ($blankLinePlaceholder) {
                            $trimmedLine = trim($line);
                            if ($trimmedLine === '---') {
                                return $blankLinePlaceholder;
                            }
                            if (preg_match('/^(#{1,6})\s+(.+)$/', $trimmedLine, $matches) && $matches[1] !== '###') {
                                return '### ' . $matches[2];
                            }
                            return $line;
                        }, $lines);
                        $lines = array_filter($lines, function($line) {
                            return trim($line) !== '';
                        });
                        $lines = array_map(function($line) use ($blankLinePlaceholder) {
                            return $line === $blankLinePlaceholder ? '' : $line;
                        }, $lines);
                        $result = implode("\n", $lines);
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove Empty Lines, Reset ### </button>
            </form>
        </div>


        <!-- Remove Markdown Form -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="markdownInput" name="markdownInput" rows="5" cols="80" placeholder="Input markdown text..."><?php 
                    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST["markdownInput"])) {
                        $text = $_POST["markdownInput"];
                        
                        $patterns = array(
                            '/#{1,6}\s/' => '',           // Headers
                            '/\*\*([^*]+)\*\*/' => '$1',  // Bold
                            '/\*([^*]+)\*/' => '$1',      // Italic
                            '/^-\s/' => '',               // List items
                            '/^>\s/' => '',               // Blockquotes
                            '/`([^`]+)`/' => '$1',        // Inline code
                            '/---+/' => '',               // Horizontal rules
                            '/\[\[([^\]]+)\]\]/' => '$1', // Remove [[brackets]]
                            '/\[([^\]]+)\]/' => '$1',     // Remove [brackets]
                        );
                        
                        $text = preg_replace(array_keys($patterns), array_values($patterns), $text);
                        
                        $lines = explode("\n", $text);
                        $result = "";
                        $prevLineEmpty = false;
                        foreach ($lines as $line) {
                            $currentLine = trim($line);
                            $currentLineEmpty = (strlen($currentLine) === 0);
                            if (!($prevLineEmpty && $currentLineEmpty)) {
                                $result .= $currentLine . "\n";
                            }
                            $prevLineEmpty = $currentLineEmpty;
                        }
                        
                        $_SESSION['results']['markdownInput'] = $result;
                        header('Location: ' . $_SERVER['PHP_SELF'] . '#markdownInput');
                        exit;
                    }
                    if (isset($_SESSION['results']['markdownInput'])) {
                        echo htmlspecialchars($_SESSION['results']['markdownInput'], ENT_QUOTES, 'UTF-8');
                        unset($_SESSION['results']['markdownInput']);
                    }
                ?></textarea>
                <button type="submit">Remove Markdown Formatting</button>
            </form>
        </div>


        <!-- Web Page Text Cleaner Form -->
        <div class="form-container">
            <form method="post" action="" target="_blank">
                <textarea id="webPageInput" name="webPageInput" rows="20" cols="200" placeholder="Paste web page text here..."></textarea>
                <button type="submit">Clean Web Page Text</button>
            </form>
        </div>

        <!-- Remove All Number Markers (Arabic & Chinese) Form -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="allNumberMarkersInput" name="allNumberMarkersInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST["allNumberMarkersInput"])) {
                        $text = $_POST["allNumberMarkersInput"];
                        
                        $lines = explode("\n", $text);
                        $result = array_map(function($line) {
                            // 1. Remove bold-wrapped Arabic number markers: **10. ...**
                            $line = preg_replace('/^(\*{2})([ \t]*\d+(?:[\.\d]*)?)([\.、\)\）\:，]|\s)+(.*)\1/u', '**$4**', $line);
                            // 2. Remove normal Arabic number markers: 10. ...
                            $line = preg_replace('/^([ \t]*)(\d+(?:[\.\d]*)?)([\.、\)\）\:，]|\s)+/u', '$1', $line);
                            // 3. Remove Chinese enumeration marker
                            if (preg_match('/^(#{1,6}\s*)?([一二三四五六七八九十百千]+)[、，](.*)/u', $line, $matches)) {
                                $header = isset($matches[1]) ? $matches[1] : '';
                                $content = $matches[3];
                                return $header . $content;
                            }
                            // 4. Normalize dash bullet spacing
                            $line = preg_replace('/^\s*-(\s+)/', '-$1', $line);
                            return $line;
                        }, $lines);
                        $result = implode("\n", $result);
                        
                        $_SESSION['results']['allNumberMarkersInput'] = $result;
                        header('Location: ' . $_SERVER['PHP_SELF'] . '#allNumberMarkersInput');
                        exit;
                    }
                    if (isset($_SESSION['results']['allNumberMarkersInput'])) {
                        echo htmlspecialchars($_SESSION['results']['allNumberMarkersInput'], ENT_QUOTES, 'UTF-8');
                        unset($_SESSION['results']['allNumberMarkersInput']);
                    }
                ?></textarea>
                <button type="submit">Remove All Number Markers</button>
            </form>
        </div>

        
        <!-- Remove dotted list -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="dottedListInput" name="dottedListInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST["dottedListInput"])) {
                        $text = $_POST["dottedListInput"];
                        $lines = explode("\n", $text);
                        $result = array_map(function($line) {
                            $line = preg_replace('/^[\t ]*- /', '', $line);
                            return trim($line);
                        }, $lines);
                        $result = array_filter($result, function($line) {
                            return !empty($line);
                        });
                        $result = implode("\n", $result);
                        $_SESSION['results']['dottedListInput'] = $result;
                        header('Location: ' . $_SERVER['PHP_SELF'] . '#dottedListInput');
                        exit;
                    }
                    if (isset($_SESSION['results']['dottedListInput'])) {
                        echo htmlspecialchars($_SESSION['results']['dottedListInput'], ENT_QUOTES, 'UTF-8');
                        unset($_SESSION['results']['dottedListInput']);
                    }
                ?></textarea>
                <button type="submit">Remove dotted list</button>
            </form>
        </div>


    </div>
</body>
</html>
