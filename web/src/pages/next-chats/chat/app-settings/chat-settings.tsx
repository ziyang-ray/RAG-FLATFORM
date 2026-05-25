import { Button } from '@/components/ui/button';
import { Form } from '@/components/ui/form';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { DatasetMetadata } from '@/constants/chat';
import { useSetModalState } from '@/hooks/common-hooks';
import { useFetchChat, useUpdateChat } from '@/hooks/use-chat-request';
import { cn } from '@/lib/utils';
import {
  removeUselessFieldsFromValues,
  setLLMSettingEnabledValues,
} from '@/utils/form';
import { zodResolver } from '@hookform/resolvers/zod';
import { isEmpty, omit } from 'lodash';
import { LucidePanelRightClose, LucideSettings, LucideDatabase, LucideChevronLeft } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { z } from 'zod';
import ChatBasicSetting from './chat-basic-settings';
import { ChatModelSettings } from './chat-model-settings';
import { ChatPromptEngine } from './chat-prompt-engine';
import { SavingButton } from './saving-button';
import { useChatSettingSchema } from './use-chat-setting-schema';

type ChatSettingsProps = { hasSingleChatBox: boolean };

export function ChatSettings({ hasSingleChatBox }: ChatSettingsProps) {
  const formSchema = useChatSettingSchema();
  const { data } = useFetchChat();
  const { updateChat, loading } = useUpdateChat();
  const { id } = useParams();
  const { t } = useTranslation();

  const { visible: settingVisible, switchVisible: switchSettingVisible } =
    useSetModalState(false);

  // 新增：知识库选择面板状态
  const [knowledgeBaseVisible, setKnowledgeBaseVisible] = useState(false);

  type FormSchemaType = z.infer<typeof formSchema>;

  const form = useForm<FormSchemaType>({
    resolver: zodResolver(formSchema),
    shouldUnregister: false,
    defaultValues: {
      name: '',
      icon: '',
      description: '',
      dataset_ids: [],
      prompt_config: {
        quote: true,
        keyword: false,
        tts: false,
        use_kg: false,
        refine_multiturn: true,
        system: '',
        parameters: [],
        reasoning: false,
        cross_languages: [],
        toc_enhance: false,
      },
      top_n: 8,
      similarity_threshold: 0.2,
      vector_similarity_weight: 0.2,
      top_k: 1024,
      meta_data_filter: {
        method: DatasetMetadata.Disabled,
        manual: [],
      },
    },
  });

  async function onSubmit(values: FormSchemaType) {
    const nextValues: Record<string, any> = removeUselessFieldsFromValues(
      values,
      'llm_setting.',
    );

    updateChat({
      chatId: id!,
      params: {
        ...omit(data, [
          'operator_permission',
          'tenant_id',
          'created_by',
          'create_time',
          'create_date',
          'update_time',
          'update_date',
          'id',
        ]),
        ...nextValues,
      },
    });
  }

  function onInvalid(errors: any) {
    void errors;
  }

  useEffect(() => {
    const llmSettingEnabledValues = setLLMSettingEnabledValues(
      data.llm_setting,
    );
    const nextData = {
      ...data,
      ...llmSettingEnabledValues,
    };

    if (!isEmpty(data)) {
      form.reset(nextData as FormSchemaType);
    }
  }, [data, form]);

  return (
    <>
      {/* 知识库选择按钮 - 固定在左侧 */}
      <div className="p-5">
        <Button
          onClick={() => setKnowledgeBaseVisible(true)}
          variant={'ghost'}
          size="icon"
          className="bg-blue-50 hover:bg-blue-100 text-blue-600 rounded-full p-2"
          data-testid="knowledge-base-toggle"
        >
          <LucideDatabase className="size-5" />
        </Button>
      </div>

      {/* 知识库选择面板 */}
      {knowledgeBaseVisible && (
        <section
          className="w-[320px] flex-shrink-0 flex flex-col bg-white border-l border-gray-200 shadow-lg z-10"
          data-testid="knowledge-base-panel"
        >
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit, onInvalid)}
              className="h-full flex flex-col"
            >
              <div className="p-5 pb-2 flex justify-between items-center text-base border-b border-gray-200">
                <span className="font-medium">{t('chat.knowledgeBases')}</span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-gray-500 hover:text-gray-700"
                  type="button"
                  onClick={() => setKnowledgeBaseVisible(false)}
                >
                  <LucideChevronLeft className="size-4" />
                </Button>
              </div>
              <div className="flex-1 overflow-auto p-5">
                <ChatBasicSetting />
              </div>
              <div className="p-5 border-t border-gray-200">
                <SavingButton loading={loading} />
              </div>
            </form>
          </Form>
        </section>
      )}

      {/* 原有的设置面板 */}
      {settingVisible || (
        <div className="p-5">
          <Button
            onClick={switchSettingVisible}
            disabled={!hasSingleChatBox}
            variant={'ghost'}
            size="icon-sm"
            data-testid="chat-settings"
          >
            <LucideSettings />
          </Button>
        </div>
      )}

      <section
        data-testid="chat-detail-settings"
        className={cn(
          'transition-[width] ease-out duration-300 flex-shrink-0 flex flex-col overflow-hidden',
          settingVisible ? 'w-[440px]' : 'w-0',
        )}
      >
        {settingVisible && (
          <>
            <div className="p-5 pb-2 flex justify-between items-center text-base">
              {t('chat.chatSetting')}

              <Button
                variant="transparent"
                size="icon-sm"
                className="border-0"
                onClick={switchSettingVisible}
                data-testid="chat-detail-settings-close"
              >
                <LucidePanelRightClose
                  className="size-4 cursor-pointer"
                  onClick={switchSettingVisible}
                />
              </Button>
            </div>

            <ScrollArea className="flex-1 px-5 pb-20">
              <Form {...form}>
                <form
                  onSubmit={form.handleSubmit(onSubmit, onInvalid)}
                  className="space-y-8"
                >
                  <ChatBasicSetting />
                  <ChatModelSettings />
                  <ChatPromptEngine />
                  <Separator />
                </form>
              </Form>
            </ScrollArea>

            <div className="px-5 py-4 absolute bottom-0 w-full bg-white border-t border-border-default">
              <SavingButton loading={loading} />
            </div>
          </>
        )}
      </section>
    </>
  );
}