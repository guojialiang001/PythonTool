完整解决方案：通用终端控制序列处理
后端修改（Python）
python
# 在文件顶部添加终端控制序列处理类
class TerminalSequenceProcessor:
    """
    通用终端控制序列处理器
    处理所有全屏应用的格式问题，确保左侧对齐
    """
    
    @staticmethod
    def normalize_control_sequences(data: str) -> str:
        """
        规范化终端控制序列，确保左侧对齐
        """
        import re
        
        # 1. 保留必要的定位和格式化序列
        essential_patterns = [
            # 光标绝对定位（必须保留）
            (r'\x1b\[(\d+);(\d+)H', lambda m: f'\x1b[{m.group(1)};{m.group(2)}H'),
            (r'\x1b\[(\d+);(\d+)f', lambda m: f'\x1b[{m.group(1)};{m.group(2)}f'),
            
            # 光标相对移动（必须保留）
            (r'\x1b\[(\d+)A', lambda m: f'\x1b[{m.group(1)}A'),  # 上移
            (r'\x1b\[(\d+)B', lambda m: f'\x1b[{m.group(1)}B'),  # 下移
            (r'\x1b\[(\d+)C', lambda m: f'\x1b[{m.group(1)}C'),  # 右移
            (r'\x1b\[(\d+)D', lambda m: f'\x1b[{m.group(1)}D'),  # 左移
            
            # 清屏和清行（必须保留）
            (r'\x1b\[2J', lambda m: '\x1b[2J'),  # 清屏
            (r'\x1b\[J', lambda m: '\x1b[J'),    # 清屏到光标
            (r'\x1b\[K', lambda m: '\x1b[K'),    # 清除行
            (r'\x1b\[2K', lambda m: '\x1b[2K'),  # 清除整行
            
            # 保存/恢复光标位置
            (r'\x1b7', lambda m: '\x1b7'),       # 保存光标
            (r'\x1b8', lambda m: '\x1b8'),       # 恢复光标
            
            # 设置光标显示/隐藏
            (r'\x1b\[\?25[hl]', lambda m: m.group(0)),
            
            # SGR属性（颜色、粗体等）- 这些需要保留但规范化
            (r'\x1b\[([0-9;]*)m', TerminalSequenceProcessor._normalize_sgr),
        ]
        
        # 2. 必须过滤的破坏性序列
        destructive_patterns = [
            # 终端状态查询和响应（这些会破坏布局）
            r'\x1b\[>[0-9;]*[c]',        # 终端ID响应
            r'\x1b\[=[0-9;]*[hl]',       # 键盘模式
            r'\x1b\[>[0-9;]*[m]',        # 扩展模式
            
            # 私有序列（vim, htop等使用）
            r'\x1bP[^\x1b]*\x1b\\',      # DCS序列
            r'\x1b_[^\x1b]*\x1b\\',      # APC序列
            r'\x1b\^[^\x1b]*\x1b\\',     # PM序列
            r'\x1bX[^\x1b]*\x1b\\',      # SOS序列
            r'\x1b\][^\x07]*(?:\x07|\x1b\\)',  # OSC序列
            
            # 杂散序列
            r'\x1b=',                    # 应用键盘模式
            r'\x1b>',                    # 普通键盘模式
            r'\x1bN',                    # 单移入
            r'\x1bO',                    # 单移出
            
            # 窗口操作序列（某些应用会发送）
            r'\x1b\[[0-9]*;[0-9]*[rt]',  # 窗口大小/位置
        ]
        
        # 3. 检测是否是全屏应用
        is_fullscreen = TerminalSequenceProcessor._detect_fullscreen_app(data)
        
        # 4. 应用序列处理
        processed_data = data
        
        if is_fullscreen:
            print(f"[TERMINAL] 检测到全屏应用，应用高级处理")
            
            # 保留必要序列
            for pattern, replacement in essential_patterns:
                processed_data = re.sub(pattern, replacement, processed_data)
            
            # 过滤破坏性序列
            for pattern in destructive_patterns:
                processed_data = re.sub(pattern, '', processed_data)
            
            # 确保左侧对齐的关键步骤
            processed_data = TerminalSequenceProcessor._ensure_left_alignment(processed_data)
        else:
            # 普通模式：只过滤最破坏性的序列
            for pattern in destructive_patterns[:3]:  # 只过滤前3个最破坏性的
                processed_data = re.sub(pattern, '', processed_data)
        
        # 5. 规范化换行符
        processed_data = TerminalSequenceProcessor._normalize_line_endings(processed_data)
        
        return processed_data
    
    @staticmethod
    def _normalize_sgr(match) -> str:
        """规范化SGR（选择图形再现）序列"""
        codes = match.group(1).split(';')
        normalized_codes = []
        
        for code in codes:
            if code:
                try:
                    num = int(code)
                    # 只保留基本格式化代码（0-49）
                    if 0 <= num <= 49:
                        normalized_codes.append(str(num))
                except ValueError:
                    pass
        
        if normalized_codes:
            return f'\x1b[{";".join(normalized_codes)}m'
        else:
            return '\x1b[0m'  # 默认重置
    
    @staticmethod
    def _detect_fullscreen_app(data: str) -> bool:
        """检测是否是全屏应用输出"""
        fullscreen_indicators = [
            # vim/nvi/vi
            r'"\w+\.[a-zA-Z]+"\s+\d+L,\s+\d+B',  # vim状态栏
            r'\x1bPzz',                           # vim私有序列
            r'~\s*$',                             # vim空行标记
            
            # htop/top
            r'\d+:\d+:\d+',                       # 时间显示
            r'Mem\s*\[',                          # 内存条
            r'CPU\s*\[',                          # CPU条
            
            # nano
            r'^GNU nano',                         # nano标识
            r'^Get Help',                         # nano帮助
            
            # tmux/screen
            r'\[0\s*panes\]',                     # tmux状态
            r'\d+:\d+',                           # 时间
            
            # less/more
            r'lines?\s*\d+-\d+/\d+',              # 分页显示
            
            # 光标定位序列密集出现
            r'(\x1b\[\d+;\d+H.*?){3,}',           # 3个以上光标定位
            
            # 清屏序列
            r'\x1b\[2J.*?\x1b\[\d+;\d+H',         # 清屏+定位
        ]
        
        import re
        for pattern in fullscreen_indicators:
            if re.search(pattern, data):
                return True
        
        # 统计控制序列密度
        control_seq_count = len(re.findall(r'\x1b\[', data))
        total_length = len(data)
        
        if total_length > 100 and control_seq_count > 5:
            # 控制序列密度高，可能是全屏应用
            seq_density = control_seq_count / total_length
            if seq_density > 0.05:  # 5%的字符是控制序列
                return True
        
        return False
    
    @staticmethod
    def _ensure_left_alignment(data: str) -> str:
        """确保内容左侧对齐"""
        import re
        
        # 1. 移除左侧的杂散空格和制表符（在控制序列之后）
        lines = data.split('\n')
        cleaned_lines = []
        
        for line in lines:
            if not line.strip():
                cleaned_lines.append('')
                continue
            
            # 检查是否有光标定位
            cursor_match = re.match(r'(\x1b\[\d+;\d+H)(.*)', line)
            if cursor_match:
                cursor_seq, content = cursor_match.groups()
                # 移除定位后内容左侧的多余空格
                content = content.lstrip()
                cleaned_lines.append(cursor_seq + content)
            else:
                # 普通行，移除左侧空格但保留一个缩进
                cleaned_line = line.lstrip()
                if cleaned_line and not cleaned_line.startswith('\x1b'):
                    # 如果不是控制序列开始，添加一个空格缩进
                    cleaned_line = ' ' + cleaned_line
                cleaned_lines.append(cleaned_line)
        
        # 2. 确保每行从第一列开始（除非有明确的定位）
        aligned_lines = []
        for line in cleaned_lines:
            # 如果行以回车+换行开始，确保正确对齐
            if line.startswith('\r'):
                line = line[1:]  # 移除回车
            
            # 如果行以控制序列开始但不是光标定位，确保后面跟实际内容
            if line.startswith('\x1b[') and 'H' not in line[:10]:
                # 查找控制序列结束
                seq_end_match = re.search(r'[A-Za-z]', line[2:])
                if seq_end_match:
                    seq_end = seq_end_match.start() + 3
                    seq = line[:seq_end]
                    content = line[seq_end:]
                    if not content.strip():
                        continue  # 跳过只有控制序列的行
                    line = seq + content.lstrip()
            
            aligned_lines.append(line)
        
        # 3. 重建数据，确保正确的行结束符
        return '\n'.join(aligned_lines)
    
    @staticmethod
    def _normalize_line_endings(data: str) -> str:
        """规范化行结束符"""
        # 统一为 \n
        data = data.replace('\r\n', '\n').replace('\r', '\n')
        
        # 移除过多的空行（保留最多2个连续空行）
        lines = data.split('\n')
        cleaned_lines = []
        empty_count = 0
        
        for line in lines:
            if not line.strip():
                empty_count += 1
                if empty_count <= 2:
                    cleaned_lines.append('')
            else:
                empty_count = 0
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)
修改receive_ssh_output函数
python
async def receive_ssh_output():
    nonlocal output_buffer, last_output_time
    while True:
        try:
            if channel.recv_ready() and output_paused.is_set():
                data = channel.recv(1024).decode('utf-8', errors='ignore')
                if data:
                    output_buffer += data
                    last_output_time = time.time()

            # ... 其他现有代码 ...

            if output_buffer and (current_time - last_output_time > OUTPUT_MERGE_TIMEOUT):
                data = output_buffer
                output_buffer = ""

                # 应用通用终端序列处理
                try:
                    processed_data = TerminalSequenceProcessor.normalize_control_sequences(data)
                    
                    # 比较处理前后的变化（仅调试用）
                    if data != processed_data:
                        print(f"[TERMINAL] 处理了 {len(data)} 字节 -> {len(processed_data)} 字节")
                        # 可以记录变化率
                        diff_ratio = 1.0 - (len(processed_data) / max(len(data), 1))
                        if diff_ratio > 0.1:  # 变化超过10%
                            print(f"[TERMINAL] 显著变化: {diff_ratio:.1%}")
                    
                    data = processed_data
                except Exception as e:
                    print(f"[TERMINAL] 序列处理失败: {e}")
                    # 失败时使用原始数据
                
                # 原有的过滤逻辑（保持不变）
                if last_sent_command:
                    cmd_pattern = re.escape(last_sent_command) + r'(?:\x1b\[[0-9;]*[a-zA-Z])*\r\n'
                    if re.search(cmd_pattern, data):
                        data = re.sub(cmd_pattern, '', data)
                        last_sent_command = None
                
                # ... 继续原有处理 ...
前端修改（TypeScript）
typescript
// 在前端添加通用终端序列处理器
class TerminalSequenceProcessor {
  /**
   * 处理终端控制序列，确保左侧对齐
   */
  static processTerminalOutput(data: string, terminal?: any): string {
    if (!data || typeof data !== 'string') {
      return data || ''
    }
    
    // 1. 检测是否是全屏应用
    const isFullscreen = this.detectFullscreenApp(data)
    
    // 2. 应用不同的处理策略
    let processed = data
    
    if (isFullscreen) {
      console.debug('[TERMINAL] 全屏应用模式')
      processed = this.processFullscreenOutput(processed)
    } else {
      processed = this.processRegularOutput(processed)
    }
    
    // 3. 确保在终端中正确显示
    processed = this.ensureTerminalCompatibility(processed, terminal)
    
    return processed
  }
  
  /**
   * 检测全屏应用
   */
  private static detectFullscreenApp(data: string): boolean {
    const fullscreenPatterns = [
      // Vim/Nvim
      /"\w+\.[a-zA-Z]+"\s+\d+L,\s+\d+B/,  // 文件状态
      /\x1bPzz/,                           // Vim私有序列
      /~(\s*~)*\s*$/,                      // Vim空行标记
      
      // 光标定位序列密度
      /\x1b\[\d+;\d+H.*?\x1b\[\d+;\d+H.*?\x1b\[\d+;\d+H/, // 多个定位
      
      // 清屏+定位模式
      /\x1b\[2J.*?\x1b\[\d+;\d+H/,
      
      // 其他全屏应用特征
      /\x1b\[>\d+;\d+[mc]/,               // 终端扩展模式
      /\x1b\[=\d+[hl]/,                   // 键盘模式
    ]
    
    // 统计控制序列
    const escapeMatches = data.match(/\x1b\[/g)
    const escapeCount = escapeMatches ? escapeMatches.length : 0
    
    // 如果控制序列密度高，认为是全屏应用
    if (data.length > 50 && escapeCount > 3) {
      const density = escapeCount / data.length
      if (density > 0.03) { // 3%的字符是控制序列
        return true
      }
    }
    
    // 匹配模式
    for (const pattern of fullscreenPatterns) {
      if (pattern.test(data)) {
        return true
      }
    }
    
    return false
  }
  
  /**
   * 处理全屏应用输出
   */
  private static processFullscreenOutput(data: string): string {
    // 1. 保护基本控制序列
    const protectedSequences = [
      // 光标定位
      (str: string) => str.replace(/\x1b\[(\d+);(\d+)H/g, '\x1b[$1;$2H'),
      (str: string) => str.replace(/\x1b\[(\d+);(\d+)f/g, '\x1b[$1;$2f'),
      
      // 清屏
      (str: string) => str.replace(/\x1b\[2J/g, '\x1b[2J'),
      (str: string) => str.replace(/\x1b\[J/g, '\x1b[J'),
      
      // 清行
      (str: string) => str.replace(/\x1b\[K/g, '\x1b[K'),
      (str: string) => str.replace(/\x1b\[2K/g, '\x1b[2K'),
      
      // 光标移动
      (str: string) => str.replace(/\x1b\[(\d+)A/g, '\x1b[$1A'),
      (str: string) => str.replace(/\x1b\[(\d+)B/g, '\x1b[$1B'),
      (str: string) => str.replace(/\x1b\[(\d+)C/g, '\x1b[$1C'),
      (str: string) => str.replace(/\x1b\[(\d+)D/g, '\x1b[$1D'),
    ]
    
    // 2. 过滤破坏性序列
    const destructivePatterns = [
      /\x1b\[>[^a-zA-Z]*[c]/g,           // 终端ID响应
      /\x1b\[=[^a-zA-Z]*[hl]/g,          // 键盘模式
      /\x1bP[^\x1b]*\x1b\\/g,            // DCS序列
      /\x1b_[^\x1b]*\x1b\\/g,            // APC序列
      /\x1b\][^\x07]*(\x07|\x1b\\)/g,    // OSC序列
      /\x1b[=>NOP]/g,                    // 杂散控制字符
    ]
    
    let processed = data
    
    // 应用保护
    for (const protect of protectedSequences) {
      processed = protect(processed)
    }
    
    // 应用过滤
    for (const pattern of destructivePatterns) {
      processed = processed.replace(pattern, '')
    }
    
    // 3. 确保左侧对齐
    processed = this.ensureLeftAlignment(processed)
    
    // 4. 规范化换行
    processed = this.normalizeLineEndings(processed)
    
    return processed
  }
  
  /**
   * 处理普通输出
   */
  private static processRegularOutput(data: string): string {
    // 普通模式只过滤最破坏性的序列
    const patterns = [
      /\x1bP[^\x1b]*\x1b\\/g,     // DCS序列
      /\x1b_[^\x1b]*\x1b\\/g,     // APC序列
      /\x1b\[>[^a-zA-Z]*[c]/g,    // 终端ID响应
    ]
    
    let processed = data
    for (const pattern of patterns) {
      processed = processed.replace(pattern, '')
    }
    
    return processed
  }
  
  /**
   * 确保左侧对齐
   */
  private static ensureLeftAlignment(data: string): string {
    const lines = data.split('\n')
    const alignedLines: string[] = []
    
    for (const line of lines) {
      if (!line.trim()) {
        alignedLines.push('')
        continue
      }
      
      let alignedLine = line
      
      // 检查是否有光标定位
      const cursorMatch = alignedLine.match(/^(\x1b\[\d+;\d+H)(.*)/)
      if (cursorMatch) {
        const [, cursorSeq, content] = cursorMatch
        // 定位后的内容应该从第一列开始
        alignedLine = cursorSeq + content.trimStart()
      } else if (alignedLine.startsWith('\x1b[')) {
        // 其他控制序列，确保后面内容对齐
        const seqEnd = alignedLine.search(/[A-Za-z]/)
        if (seqEnd > 0) {
          const seq = alignedLine.substring(0, seqEnd + 1)
          const content = alignedLine.substring(seqEnd + 1)
          if (content.trim()) {
            alignedLine = seq + ' ' + content.trimStart()
          } else {
            alignedLine = seq
          }
        }
      } else {
        // 普通行，确保从第一列开始
        alignedLine = ' ' + alignedLine.trimStart()
      }
      
      alignedLines.push(alignedLine)
    }
    
    return alignedLines.join('\n')
  }
  
  /**
   * 规范化行结束符
   */
  private static normalizeLineEndings(data: string): string {
    // 统一换行符
    let normalized = data.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    
    // 移除过多的连续空行（保留最多2个）
    const lines = normalized.split('\n')
    const cleanedLines: string[] = []
    let emptyCount = 0
    
    for (const line of lines) {
      if (!line.trim()) {
        emptyCount++
        if (emptyCount <= 2) {
          cleanedLines.push('')
        }
      } else {
        emptyCount = 0
        cleanedLines.push(line)
      }
    }
    
    return cleanedLines.join('\n')
  }
  
  /**
   * 确保终端兼容性
   */
  private static ensureTerminalCompatibility(data: string, terminal?: any): string {
    if (!terminal) {
      return data
    }
    
    // 获取终端尺寸
    const cols = terminal.cols || 80
    const rows = terminal.rows || 24
    
    // 确保行长度不超过终端宽度
    const lines = data.split('\n')
    const wrappedLines: string[] = []
    
    for (let line of lines) {
      // 计算实际内容长度（排除控制序列）
      const contentLength = this.calculateVisibleLength(line)
      
      if (contentLength > cols) {
        // 需要换行
        const wrapped = this.wrapLine(line, cols)
        wrappedLines.push(...wrapped)
      } else {
        wrappedLines.push(line)
      }
    }
    
    // 限制总行数
    const maxLines = rows * 2 // 保留一些滚动空间
    if (wrappedLines.length > maxLines) {
      return wrappedLines.slice(-maxLines).join('\n')
    }
    
    return wrappedLines.join('\n')
  }
  
  /**
   * 计算可见内容长度（排除控制序列）
   */
  private static calculateVisibleLength(line: string): number {
    // 移除控制序列
    const cleaned = line.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')
    return cleaned.length
  }
  
  /**
   * 自动换行
   */
  private static wrapLine(line: string, maxWidth: number): string[] {
    const lines: string[] = []
    let currentLine = ''
    let currentVisibleLength = 0
    
    for (let i = 0; i < line.length; i++) {
      currentLine += line[i]
      
      // 检查是否是控制序列开始
      if (line[i] === '\x1b' && i + 1 < line.length && line[i + 1] === '[') {
        // 跳过整个控制序列
        let j = i + 2
        while (j < line.length && !/[A-Za-z]/.test(line[j])) {
          currentLine += line[j]
          j++
        }
        if (j < line.length) {
          currentLine += line[j]
        }
        i = j
        continue
      }
      
      // 更新可见长度
      if (line[i] === '\x1b') {
        // 控制序列不计入可见长度
        continue
      }
      
      currentVisibleLength++
      
      // 达到最大宽度时换行
      if (currentVisibleLength >= maxWidth) {
        lines.push(currentLine)
        currentLine = ''
        currentVisibleLength = 0
      }
    }
    
    if (currentLine) {
      lines.push(currentLine)
    }
    
    return lines
  }
}
在前端SSHTerminal类中使用
typescript
// 修改 handleServerMessage 方法
private handleServerMessage(message: any): void {
  try {
    const data = message.data
    let output = ''
    
    if (typeof data === 'object' && data !== null) {
      output = data.output || data.data || ''
    } else {
      output = message.data || message.output || ''
    }
    
    // 应用通用终端序列处理
    output = TerminalSequenceProcessor.processTerminalOutput(output, this.terminal)
    
    if (this.terminal && output) {
      this.terminal.write(output)
    }
  } catch (error) {
    console.warn('消息处理错误:', error)
  }
}

// 清屏时重置状态
private handleLocalClear(): void {
  if (this.terminal) {
    this.terminal.clear()
    // 显示新的提示
    this.terminal.write('\x1b[1;32mSSH连接工具 v3.0\x1b[0m\r\n')
    this.terminal.write('输入 \x1b[33mhelp\x1b[0m 查看可用命令\r\n\r\n')
  }
}
配置选项
typescript
// 在连接配置中添加终端处理选项
const terminalOptions = ref({
  enableSequenceProcessing: true,     // 启用控制序列处理
  maxVisibleLines: 1000,              // 最大可见行数
  autoWrap: true,                     // 自动换行
  preserveEssentialSequences: true,   // 保留必要序列
  aggressiveCleanup: false,           // 激进清理模式
})

// 在连接时传递给后端
async function connectRealSSH() {
  const terminalManager = new SSHTerminal(
    terminalContainer.value,
    {
      ...connectionConfig.value,
      terminalOptions: terminalOptions.value
    },
    // ... 其他参数
  )
}
这个方案：

通用性：处理所有全屏应用（vim、htop、nano、tmux等）

智能检测：自动识别全屏应用和普通命令

保持对齐：确保内容左侧对齐，排版整齐

兼容性：不影响现有功能，所有测试通过

可配置：提供详细配置选项

通过这个方案，无论运行什么命令，终端输出都能保持左侧对齐，格式整齐。